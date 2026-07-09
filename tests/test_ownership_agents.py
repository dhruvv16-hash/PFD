import os
import unittest
import uuid
from datetime import datetime, timezone
from config.settings import settings
from db.connection import init_db, get_connection, transaction
from logger.logger import setup_global_logger
from agents.ownership_agents import (
    PromoterAgent, FiiAgent, DiiAgent, MutualFundAgent, PublicAgent, PledgeAgent
)
from agents.base import AgentValidationError

# Override database path for testing
TEST_DB_PATH = os.path.join(os.path.dirname(__file__), "test_ownership.db")
settings.db_path = TEST_DB_PATH

class TestOwnershipAgents(unittest.TestCase):
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

        self.execution_id = str(uuid.uuid4())
        
        # Pre-seed pipeline executions
        with transaction() as conn:
            conn.execute(
                "INSERT INTO pipeline_executions (execution_id, status, started_at) VALUES (?, 'Running', ?)",
                (self.execution_id, datetime.now(timezone.utc).isoformat())
            )
            # Pre-seed one company: TCS
            conn.execute(
                """
                INSERT INTO companies (company_uuid, symbol, isin, name, exchange, status, last_updated, source)
                VALUES ('uuid-tcs', 'TCS', 'INE467B01029', 'Tata Consultancy Services', 'NSE', 'Active', ?, 'Seed')
                """,
                (datetime.now(timezone.utc).isoformat(),)
            )

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except OSError:
                pass

    def test_all_ownership_agents_write_correctly(self):
        agents = [
            PromoterAgent(), FiiAgent(), DiiAgent(),
            MutualFundAgent(), PublicAgent(), PledgeAgent()
        ]
        
        for agent in agents:
            outputs = agent.run(self.execution_id, {"execution_id": self.execution_id})
            self.assertEqual(outputs["status"], "success")
            self.assertEqual(outputs["metrics"]["records_processed"], 1)

        conn = get_connection()
        # Verify Promoter holdings
        p_row = conn.execute("SELECT * FROM promoter_holdings WHERE company_uuid = 'uuid-tcs' AND is_current = 1").fetchone()
        self.assertIsNotNone(p_row)
        self.assertEqual(p_row["promoter_holding_pct"], 72.41) # TCS 2026-Q2 hardcoded value
        self.assertEqual(p_row["quarter"], "2026-Q2")

        # Verify FII holdings
        f_row = conn.execute("SELECT * FROM fii_holdings WHERE company_uuid = 'uuid-tcs' AND is_current = 1").fetchone()
        self.assertEqual(f_row["fii_holding_pct"], 12.52)

        # Verify DII holdings
        d_row = conn.execute("SELECT * FROM dii_holdings WHERE company_uuid = 'uuid-tcs' AND is_current = 1").fetchone()
        self.assertEqual(d_row["dii_holding_pct"], 10.20)

        # Verify MF holdings
        m_row = conn.execute("SELECT * FROM mutual_fund_holdings WHERE company_uuid = 'uuid-tcs' AND is_current = 1").fetchone()
        self.assertEqual(m_row["mf_holding_pct"], 6.25)

        # Verify Public holdings
        pub_row = conn.execute("SELECT * FROM public_holdings WHERE company_uuid = 'uuid-tcs' AND is_current = 1").fetchone()
        self.assertEqual(pub_row["public_holding_pct"], 4.87)

        # Verify Pledge holdings
        pledge_row = conn.execute("SELECT * FROM promoter_pledges WHERE company_uuid = 'uuid-tcs' AND is_current = 1").fetchone()
        self.assertEqual(pledge_row["pledged_pct"], 0.00)
        conn.close()

    def test_version_archiving(self):
        agent = PromoterAgent()
        
        # 1. Run for Q1
        with transaction() as conn:
            # Seed Q1 runner inputs
            conn.execute("UPDATE pipeline_executions SET status = 'Running'")
        
        # We can pass quarter='2026-Q1' in initial_inputs
        inputs_q1 = {
            "execution_id": self.execution_id,
            "pipeline_state": {
                "initial_inputs": {"quarter": "2026-Q1"}
            }
        }
        agent.run(self.execution_id, inputs_q1)

        # 2. Run for Q1 again with a different execution ID (simulate re-run / update)
        other_execution_id = str(uuid.uuid4())
        with transaction() as conn:
            conn.execute(
                "INSERT INTO pipeline_executions (execution_id, status, started_at) VALUES (?, 'Running', ?)",
                (other_execution_id, datetime.now(timezone.utc).isoformat())
            )
        
        inputs_q1_retry = {
            "execution_id": other_execution_id,
            "pipeline_state": {
                "initial_inputs": {"quarter": "2026-Q1"}
            }
        }
        agent.run(other_execution_id, inputs_q1_retry)

        conn = get_connection()
        rows = conn.execute(
            "SELECT is_current, execution_id FROM promoter_holdings WHERE company_uuid = 'uuid-tcs' AND quarter = '2026-Q1' ORDER BY id"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        
        # First execution deprecated
        self.assertEqual(rows[0]["is_current"], 0)
        self.assertEqual(rows[0]["execution_id"], self.execution_id)

        # Second execution current
        self.assertEqual(rows[1]["is_current"], 1)
        self.assertEqual(rows[1]["execution_id"], other_execution_id)
        conn.close()

    def test_validation_ranges(self):
        # Seed invalid values to verify that validator catches errors
        agent = PromoterAgent()
        
        # We will temporarily override get_ownership_data to return an invalid value
        import agents.ownership_agents
        original_get = agents.ownership_agents.get_ownership_data
        
        try:
            agents.ownership_agents.get_ownership_data = lambda s, c, q: {
                "promoter": -5.2, "promoter_group": 0.0, "fii": 0.0, "dii": 0.0, "mf": 0.0, "public": 0.0, "pledge": 0.0
            }
            with self.assertRaises(AgentValidationError):
                agent.run(self.execution_id, {"execution_id": self.execution_id})
                
            agents.ownership_agents.get_ownership_data = lambda s, c, q: {
                "promoter": 105.0, "promoter_group": 0.0, "fii": 0.0, "dii": 0.0, "mf": 0.0, "public": 0.0, "pledge": 0.0
            }
            with self.assertRaises(AgentValidationError):
                agent.run(self.execution_id, {"execution_id": self.execution_id})
        finally:
            agents.ownership_agents.get_ownership_data = original_get

if __name__ == "__main__":
    unittest.main()
