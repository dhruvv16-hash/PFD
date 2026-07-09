import os
import unittest
import uuid
from datetime import datetime, timezone
from config.settings import settings
from db.connection import init_db, get_connection, transaction
from logger.logger import setup_global_logger
from agents.merge_agent import MergeAgent
from agents.base import AgentValidationError

# Override database path for testing
TEST_DB_PATH = os.path.join(os.path.dirname(__file__), "test_merge.db")
settings.db_path = TEST_DB_PATH

class TestMergeAgent(unittest.TestCase):
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
        self.agent = MergeAgent()
        
        # Pre-seed pipeline executions
        with transaction() as conn:
            conn.execute(
                "INSERT INTO pipeline_executions (execution_id, status, started_at) VALUES (?, 'Running', ?)",
                (self.execution_id, datetime.now(timezone.utc).isoformat())
            )
            # Pre-seed company: INFY
            conn.execute(
                """
                INSERT INTO companies (company_uuid, symbol, isin, name, exchange, status, last_updated, source)
                VALUES ('uuid-infy', 'INFY', 'INE009A01021', 'Infosys Limited', 'NSE', 'Active', ?, 'Seed')
                """,
                (datetime.now(timezone.utc).isoformat(),)
            )

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except OSError:
                pass

    def _seed_raw_holdings(self, promoter=14.95, fii=33.62, dii=35.25, mf=18.35, public=16.18, pledge=0.0, quarter="2026-Q2"):
        timestamp_now = datetime.now(timezone.utc).isoformat()
        with transaction() as conn:
            # Seed promoter holding
            conn.execute(
                "INSERT INTO promoter_holdings (company_uuid, quarter, promoter_holding_pct, promoter_group_holding_pct, is_current, created_at, execution_id, source) VALUES (?, ?, ?, ?, 1, ?, ?, 'discl')",
                ("uuid-infy", quarter, promoter, promoter * 0.1, timestamp_now, self.execution_id)
            )
            # Seed FII holding
            conn.execute(
                "INSERT INTO fii_holdings (company_uuid, quarter, fii_holding_pct, is_current, created_at, execution_id, source) VALUES (?, ?, ?, 1, ?, ?, 'discl')",
                ("uuid-infy", quarter, fii, timestamp_now, self.execution_id)
            )
            # Seed DII holding
            conn.execute(
                "INSERT INTO dii_holdings (company_uuid, quarter, dii_holding_pct, is_current, created_at, execution_id, source) VALUES (?, ?, ?, 1, ?, ?, 'discl')",
                ("uuid-infy", quarter, dii, timestamp_now, self.execution_id)
            )
            # Seed MF holding
            conn.execute(
                "INSERT INTO mutual_fund_holdings (company_uuid, quarter, mf_holding_pct, is_current, created_at, execution_id, source) VALUES (?, ?, ?, 1, ?, ?, 'discl')",
                ("uuid-infy", quarter, mf, timestamp_now, self.execution_id)
            )
            # Seed Public holding
            conn.execute(
                "INSERT INTO public_holdings (company_uuid, quarter, public_holding_pct, is_current, created_at, execution_id, source) VALUES (?, ?, ?, 1, ?, ?, 'discl')",
                ("uuid-infy", quarter, public, timestamp_now, self.execution_id)
            )
            # Seed Pledge holding
            conn.execute(
                "INSERT INTO promoter_pledges (company_uuid, quarter, pledged_pct, is_current, created_at, execution_id, source) VALUES (?, ?, ?, 1, ?, ?, 'discl')",
                ("uuid-infy", quarter, pledge, timestamp_now, self.execution_id)
            )

    def test_successful_merge(self):
        self._seed_raw_holdings()
        
        outputs = self.agent.run(self.execution_id, {"execution_id": self.execution_id})
        self.assertEqual(outputs["status"], "success")
        self.assertEqual(outputs["snapshots_merged_count"], 1)

        # Check DB
        conn = get_connection()
        row = conn.execute("SELECT * FROM ownership_snapshots WHERE company_uuid = 'uuid-infy' AND is_current = 1").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["quarter"], "2026-Q2")
        self.assertEqual(row["version"], "1")
        self.assertEqual(row["promoter_holding_pct"], 14.95)
        self.assertEqual(row["fii_holding_pct"], 33.62)
        self.assertEqual(row["dii_holding_pct"], 35.25)
        self.assertEqual(row["mf_holding_pct"], 18.35)
        self.assertEqual(row["public_holding_pct"], 16.18)
        self.assertEqual(row["pledged_pct"], 0.0)
        conn.close()

    def test_snapshot_version_archiving(self):
        self._seed_raw_holdings()
        
        # First Run - Version 1
        self.agent.run(self.execution_id, {"execution_id": self.execution_id})

        # Second Run - Version 2 (simulate update)
        other_execution_id = str(uuid.uuid4())
        with transaction() as conn:
            conn.execute(
                "INSERT INTO pipeline_executions (execution_id, status, started_at) VALUES (?, 'Running', ?)",
                (other_execution_id, datetime.now(timezone.utc).isoformat())
            )
        
        self.agent.run(other_execution_id, {"execution_id": other_execution_id})

        conn = get_connection()
        rows = conn.execute(
            "SELECT version, is_current, execution_id FROM ownership_snapshots WHERE company_uuid = 'uuid-infy' AND quarter = '2026-Q2' ORDER BY version"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        
        # Version 1 is deprecated
        self.assertEqual(rows[0]["version"], "1")
        self.assertEqual(rows[0]["is_current"], 0)
        self.assertEqual(rows[0]["execution_id"], self.execution_id)

        # Version 2 is active
        self.assertEqual(rows[1]["version"], "2")
        self.assertEqual(rows[1]["is_current"], 1)
        self.assertEqual(rows[1]["execution_id"], other_execution_id)
        conn.close()

    def test_validation_sum_rejection(self):
        # Seed raw holdings that sum to 105% (invalid)
        self._seed_raw_holdings(promoter=20.0, fii=30.0, dii=30.0, public=25.0) # Sum = 105.0%
        
        with self.assertRaises(AgentValidationError):
            self.agent.run(self.execution_id, {"execution_id": self.execution_id})

    def test_validation_missing_data(self):
        # Seed only Promoter and FII, leaving other tables empty
        timestamp_now = datetime.now(timezone.utc).isoformat()
        with transaction() as conn:
            conn.execute(
                "INSERT INTO promoter_holdings (company_uuid, quarter, promoter_holding_pct, is_current, created_at, execution_id, source) VALUES (?, '2026-Q2', ?, 1, ?, ?, 'discl')",
                ("uuid-infy", 14.95, timestamp_now, self.execution_id)
            )
            
        with self.assertRaises(AgentValidationError):
            self.agent.run(self.execution_id, {"execution_id": self.execution_id})

if __name__ == "__main__":
    unittest.main()
