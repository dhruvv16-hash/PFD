import os
import unittest
import uuid
import json
import sqlite3
from datetime import datetime, timezone
from config.settings import settings
from db.connection import init_db, get_connection, transaction
from logger.logger import setup_global_logger
from agents.base import BaseAgent
from agents.hello_world import HelloWorldAgent
from orchestrator.orchestrator import WorkflowOrchestrator, PipelineConcurrencyError

# Ensure global logger is setup so we see test logs
setup_global_logger()

# Override database path for testing to keep production database clean
TEST_DB_PATH = os.path.join(os.path.dirname(__file__), "test_platform.db")
settings.db_path = TEST_DB_PATH
settings.retry_base_delay = 0.05  # Speed up backoff for unit tests
settings.retry_multiplier = 1.1

class MockStateAgent(BaseAgent):
    def __init__(self, name="MockStateAgent"):
        super().__init__(name=name, version="1.0.0", role="Test State")

    def execute(self, inputs: dict, job_id: str) -> dict:
        return {"value": "agent_data"}

class FailingAgent(BaseAgent):
    """Fails the first N times, then succeeds or fails depending on setup."""
    def __init__(self, fail_count=2, name="FailingAgent"):
        super().__init__(name=name, version="1.0.0", role="Simulate transient failures")
        self.fail_count = fail_count
        self.attempts = 0

    def execute(self, inputs: dict, job_id: str) -> dict:
        self.attempts += 1
        if self.attempts <= self.fail_count:
            raise ValueError(f"Transient error on attempt {self.attempts}")
        return {"status": "recovered", "attempt": self.attempts}

class TestWorkflowOrchestrator(unittest.TestCase):
    def setUp(self):
        init_db()
        with transaction() as conn:
            conn.execute("DELETE FROM evidence_records")
            conn.execute("DELETE FROM audit_logs")
            conn.execute("DELETE FROM company_profiles")
            conn.execute("DELETE FROM company_history")
            conn.execute("DELETE FROM universe_snapshots")
            conn.execute("DELETE FROM promoter_holdings")
            conn.execute("DELETE FROM fii_holdings")
            conn.execute("DELETE FROM dii_holdings")
            conn.execute("DELETE FROM mutual_fund_holdings")
            conn.execute("DELETE FROM public_holdings")
            conn.execute("DELETE FROM promoter_pledges")
            conn.execute("DELETE FROM ownership_snapshots")
            conn.execute("DELETE FROM company_quarterly_analytics")
            conn.execute("DELETE FROM analytics_reports")
            conn.execute("DELETE FROM active_trades")
            conn.execute("DELETE FROM companies")
            conn.execute("DELETE FROM job_executions")
            conn.execute("DELETE FROM pipeline_executions")

    def tearDown(self):
        # Clean up test DB after tests run
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except OSError:
                pass

    def test_pipeline_success_flow(self):
        # Run a simple HelloWorldAgent pipeline
        agent = HelloWorldAgent()
        orchestrator = WorkflowOrchestrator(agents=[agent])
        
        execution_id = str(uuid.uuid4())
        results = orchestrator.run_pipeline(
            execution_id=execution_id,
            initial_inputs={"text": "testing 1 2 3"}
        )
        
        # Verify result structure
        self.assertIn("HelloWorldAgent", results)
        self.assertEqual(results["HelloWorldAgent"]["status"], "success")
        self.assertEqual(results["HelloWorldAgent"]["message"], "Hello World! Processed text: TESTING 1 2 3")

        # Verify database logs
        conn = get_connection()
        pe = conn.execute("SELECT * FROM pipeline_executions WHERE execution_id = ?", (execution_id,)).fetchone()
        self.assertIsNotNone(pe)
        self.assertEqual(pe["status"], "Completed")

        jobs = conn.execute("SELECT * FROM job_executions WHERE execution_id = ?", (execution_id,)).fetchall()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["status"], "Completed")
        self.assertEqual(jobs[0]["agent_name"], "HelloWorldAgent")
        
        # Verify results were serialized to the database
        db_results = json.loads(jobs[0]["results"])
        self.assertEqual(db_results["status"], "success")

        # Verify audit logs and evidence were written
        audits = conn.execute("SELECT * FROM audit_logs WHERE job_id = ?", (jobs[0]["job_id"],)).fetchall()
        self.assertGreater(len(audits), 0)
        
        evidence = conn.execute("SELECT * FROM evidence_records WHERE job_id = ?", (jobs[0]["job_id"],)).fetchall()
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["field_name"], "mock_field_count")
        self.assertEqual(evidence[0]["value"], "42")
        conn.close()

    def test_concurrency_and_duplicate_prevention(self):
        agent = HelloWorldAgent()
        orchestrator = WorkflowOrchestrator(agents=[agent])
        execution_id = str(uuid.uuid4())

        # Pre-seed running state
        with transaction() as conn:
            conn.execute(
                "INSERT INTO pipeline_executions (execution_id, status, started_at) VALUES (?, ?, ?)",
                (execution_id, "Running", datetime.now(timezone.utc).isoformat())
            )

        # Attempt to run again with the same execution_id
        with self.assertRaises(PipelineConcurrencyError):
            orchestrator.run_pipeline(execution_id=execution_id, initial_inputs={"text": "run"})

        # Update pre-seeded run to Completed
        with transaction() as conn:
            conn.execute(
                "UPDATE pipeline_executions SET status = 'Completed', finished_at = ? WHERE execution_id = ?",
                (datetime.now(timezone.utc).isoformat(), execution_id)
            )

        # Attempt to run again should fail because it completed
        with self.assertRaises(PipelineConcurrencyError):
            orchestrator.run_pipeline(execution_id=execution_id, initial_inputs={"text": "run"})

    def test_exponential_backoff_retry(self):
        # FailingAgent will fail 2 times, then succeed on 3rd attempt. Max retries is 3.
        agent = FailingAgent(fail_count=2)
        orchestrator = WorkflowOrchestrator(agents=[agent])
        execution_id = str(uuid.uuid4())

        results = orchestrator.run_pipeline(execution_id=execution_id)
        
        # Verify it succeeded after retries
        self.assertIn("FailingAgent", results)
        self.assertEqual(results["FailingAgent"]["status"], "recovered")
        self.assertEqual(results["FailingAgent"]["attempt"], 3)

        # Verify job executions table has entries for attempts
        conn = get_connection()
        jobs = conn.execute(
            "SELECT run_number, status, error_message FROM job_executions WHERE execution_id = ? ORDER BY run_number",
            (execution_id,)
        ).fetchall()
        
        self.assertEqual(len(jobs), 3)
        self.assertEqual(jobs[0]["status"], "Failed")
        self.assertEqual(jobs[0]["run_number"], 1)
        self.assertIn("Transient error on attempt 1", jobs[0]["error_message"])

        self.assertEqual(jobs[1]["status"], "Failed")
        self.assertEqual(jobs[1]["run_number"], 2)

        self.assertEqual(jobs[2]["status"], "Completed")
        self.assertEqual(jobs[2]["run_number"], 3)
        conn.close()

    def test_failure_termination_and_resume_checkpoint(self):
        # Step 1: Run a pipeline where the first agent succeeds, but the second agent fails completely.
        agent1 = MockStateAgent(name="StepOneAgent")
        agent2 = FailingAgent(fail_count=10, name="StepTwoAgent")  # will fail all 3 retries
        
        orchestrator = WorkflowOrchestrator(agents=[agent1, agent2])
        execution_id = str(uuid.uuid4())

        with self.assertRaises(ValueError):
            orchestrator.run_pipeline(execution_id=execution_id)

        # Check DB states
        conn = get_connection()
        pe = conn.execute("SELECT status FROM pipeline_executions WHERE execution_id = ?", (execution_id,)).fetchone()
        self.assertEqual(pe["status"], "Failed")

        jobs = conn.execute("SELECT agent_name, status FROM job_executions WHERE execution_id = ?", (execution_id,)).fetchall()
        completed_agents = [j["agent_name"] for j in jobs if j["status"] == "Completed"]
        self.assertIn("StepOneAgent", completed_agents)
        self.assertNotIn("StepTwoAgent", completed_agents)
        conn.close()

        # Step 2: Fix/configure the second agent to succeed, and resume the execution from checkpoint!
        agent2_fixed = FailingAgent(fail_count=0, name="StepTwoAgent")  # succeeds immediately
        resume_orchestrator = WorkflowOrchestrator(agents=[agent1, agent2_fixed])

        # Running without resume=True on failed execution raises Error
        with self.assertRaises(PipelineConcurrencyError):
            resume_orchestrator.run_pipeline(execution_id=execution_id)

        # Running with resume=True should load StepOneAgent from DB, skip it, and run StepTwoAgent
        results = resume_orchestrator.run_pipeline(execution_id=execution_id, resume=True)
        
        self.assertIn("StepOneAgent", results)
        self.assertIn("StepTwoAgent", results)
        self.assertEqual(results["StepOneAgent"]["value"], "agent_data")
        self.assertEqual(results["StepTwoAgent"]["status"], "recovered")

        # Check DB state after resume success
        conn = get_connection()
        pe = conn.execute("SELECT status FROM pipeline_executions WHERE execution_id = ?", (execution_id,)).fetchone()
        self.assertEqual(pe["status"], "Completed")
        conn.close()

if __name__ == "__main__":
    unittest.main()
