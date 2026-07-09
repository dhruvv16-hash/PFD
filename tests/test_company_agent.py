import os
import unittest
import uuid
import json
import sqlite3
from datetime import datetime, timezone
from config.settings import settings
from db.connection import init_db, get_connection, transaction
from logger.logger import setup_global_logger
from agents.company_agent import CompanyAgent
from agents.base import AgentValidationError

# Override database path for testing
TEST_DB_PATH = os.path.join(os.path.dirname(__file__), "test_company.db")
settings.db_path = TEST_DB_PATH

class TestCompanyAgent(unittest.TestCase):
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
            
        self.agent = CompanyAgent()
        self.execution_id = str(uuid.uuid4())
        
        # Pre-seed pipeline executions to satisfy foreign key constraints
        with transaction() as conn:
            conn.execute(
                "INSERT INTO pipeline_executions (execution_id, status, started_at) VALUES (?, 'Running', ?)",
                (self.execution_id, datetime.now(timezone.utc).isoformat())
            )
            
            # Pre-seed two active companies
            conn.execute(
                """
                INSERT INTO companies (company_uuid, symbol, isin, name, exchange, status, last_updated, source)
                VALUES ('uuid-tcs', 'TCS', 'INE467B01029', 'Tata Consultancy Services', 'NSE', 'Active', ?, 'Seed')
                """,
                (datetime.now(timezone.utc).isoformat(),)
            )
            conn.execute(
                """
                INSERT INTO companies (company_uuid, symbol, isin, name, exchange, status, last_updated, source)
                VALUES ('uuid-generic', 'XYZ', 'INE999B01019', 'Generic Enterprises', 'NSE', 'Active', ?, 'Seed')
                """,
                (datetime.now(timezone.utc).isoformat(),)
            )

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except OSError:
                pass

    def test_profile_creation_and_fields(self):
        # Run company agent
        outputs = self.agent.run(self.execution_id, {"execution_id": self.execution_id})
        
        self.assertEqual(outputs["metrics"]["records_processed"], 2)
        self.assertEqual(outputs["status"], "success")

        # Verify database profiles
        conn = get_connection()
        profiles = conn.execute("SELECT * FROM company_profiles WHERE is_current = 1").fetchall()
        self.assertEqual(len(profiles), 2)
        
        tcs_profile = next(p for p in profiles if p["company_uuid"] == "uuid-tcs")
        self.assertEqual(tcs_profile["industry"], "Information Technology Services")
        self.assertEqual(tcs_profile["sector"], "Technology")
        self.assertEqual(tcs_profile["version"], "1")
        self.assertEqual(tcs_profile["is_current"], 1)
        self.assertEqual(tcs_profile["website"], "https://www.tcs.com")
        self.assertGreater(tcs_profile["market_cap"], 1000000000)

        # Verify products/services are saved as JSON
        products = json.loads(tcs_profile["products"])
        self.assertIn("TCS BaNCS", products)
        conn.close()

    def test_profile_version_control(self):
        # 1. First run - Version 1
        self.agent.run(self.execution_id, {"execution_id": self.execution_id})

        # Pre-seed secondary execution ID for the second run
        other_execution_id = str(uuid.uuid4())
        with transaction() as conn:
            conn.execute(
                "INSERT INTO pipeline_executions (execution_id, status, started_at) VALUES (?, 'Running', ?)",
                (other_execution_id, datetime.now(timezone.utc).isoformat())
            )

        # 2. Second run - Version 2
        self.agent.run(other_execution_id, {"execution_id": other_execution_id})

        conn = get_connection()
        # Verify both versions exist, but only one is active (is_current = 1)
        tcs_profiles = conn.execute(
            "SELECT version, is_current, execution_id FROM company_profiles WHERE company_uuid = 'uuid-tcs' ORDER BY version"
        ).fetchall()
        
        self.assertEqual(len(tcs_profiles), 2)
        
        # Version 1 profile is now deprecated
        self.assertEqual(tcs_profiles[0]["version"], "1")
        self.assertEqual(tcs_profiles[0]["is_current"], 0)
        self.assertEqual(tcs_profiles[0]["execution_id"], self.execution_id)

        # Version 2 profile is active
        self.assertEqual(tcs_profiles[1]["version"], "2")
        self.assertEqual(tcs_profiles[1]["is_current"], 1)
        self.assertEqual(tcs_profiles[1]["execution_id"], other_execution_id)
        conn.close()

    def test_validation_constraints(self):
        # Override _resolve_profile to return a profile missing a mandatory field
        self.agent._resolve_profile = lambda symbol, name: {
            "business_description": "Invalid Company",
            "industry": "No Sector",
            "sector": "", # Empty Sector - should fail validation!
            "website": "http://invalid.com"
        }
        
        with self.assertRaises(AgentValidationError):
            self.agent.run(self.execution_id, {"execution_id": self.execution_id})

if __name__ == "__main__":
    unittest.main()
