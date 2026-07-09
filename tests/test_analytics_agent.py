import os
import json
import unittest
import uuid
from datetime import datetime, timezone
from config.settings import settings
from db.connection import init_db, get_connection, transaction
from logger.logger import setup_global_logger
from agents.analytics_agent import AnalyticsAgent
from agents.base import AgentValidationError

# Override database path for testing
TEST_DB_PATH = os.path.join(os.path.dirname(__file__), "test_analytics.db")
settings.db_path = TEST_DB_PATH

class TestAnalyticsAgent(unittest.TestCase):
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
        self.agent = AnalyticsAgent()

        # Seed pipeline execution
        with transaction() as conn:
            conn.execute(
                "INSERT INTO pipeline_executions (execution_id, status, started_at) VALUES (?, 'Running', ?)",
                (self.execution_id, datetime.now(timezone.utc).isoformat())
            )
            # Seed 3 companies: TCS, INFY, RELIANCE
            conn.execute("INSERT INTO companies (company_uuid, symbol, isin, name, exchange, status, last_updated, source) VALUES ('uuid-tcs', 'TCS', 'INE467B01029', 'Tata Consultancy Services', 'NSE', 'Active', 'now', 'seed')")
            conn.execute("INSERT INTO companies (company_uuid, symbol, isin, name, exchange, status, last_updated, source) VALUES ('uuid-infy', 'INFY', 'INE009A01021', 'Infosys Limited', 'NSE', 'Active', 'now', 'seed')")
            conn.execute("INSERT INTO companies (company_uuid, symbol, isin, name, exchange, status, last_updated, source) VALUES ('uuid-reliance', 'RELIANCE', 'INE002A01018', 'Reliance Industries', 'NSE', 'Active', 'now', 'seed')")

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except OSError:
                pass

    def _seed_snapshot(self, company_uuid, quarter, promoter, fii, dii, mf, public, pledged):
        snapshot_uuid = str(uuid.uuid4())
        timestamp_now = datetime.now(timezone.utc).isoformat()
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO ownership_snapshots (
                    snapshot_uuid, company_uuid, quarter, version, is_current,
                    promoter_holding_pct, fii_holding_pct, dii_holding_pct, mf_holding_pct,
                    public_holding_pct, pledged_pct, created_at, execution_id, source
                )
                VALUES (?, ?, ?, '1', 1, ?, ?, ?, ?, ?, ?, ?, ?, 'seed')
                """,
                (snapshot_uuid, company_uuid, quarter, promoter, fii, dii, mf, public, pledged, timestamp_now, self.execution_id)
            )

    def test_delta_computation_and_rankings(self):
        # 1. Seed Q1 and Q2 snapshots for 3 companies
        # TCS: FII Buying (delta = +3.0%), Pledge Reduction (delta = -2.0%)
        self._seed_snapshot("uuid-tcs", "2026-Q1", 72.35, 12.0, 10.0, 6.0, 5.65, 5.0)
        self._seed_snapshot("uuid-tcs", "2026-Q2", 72.35, 15.0, 10.0, 6.0, 2.65, 3.0)

        # INFY: Promoter Buying (delta = +5.0%)
        self._seed_snapshot("uuid-infy", "2026-Q1", 10.0, 33.0, 35.0, 18.0, 22.0, 0.0)
        self._seed_snapshot("uuid-infy", "2026-Q2", 15.0, 33.0, 35.0, 18.0, 17.0, 0.0)

        # RELIANCE: MF Buying (delta = +4.0%)
        self._seed_snapshot("uuid-reliance", "2026-Q1", 50.0, 22.0, 16.0, 8.0, 12.0, 0.0)
        self._seed_snapshot("uuid-reliance", "2026-Q2", 50.0, 22.0, 16.0, 12.0, 12.0, 0.0)

        # 2. Run AnalyticsAgent
        outputs = self.agent.run(self.execution_id, {"execution_id": self.execution_id})
        self.assertEqual(outputs["status"], "success")
        self.assertEqual(outputs["companies_analyzed"], 3)
        self.assertEqual(outputs["top_fii_buying_leader"], "TCS")

        # 3. Verify company_quarterly_analytics entries in DB
        conn = get_connection()
        tcs_analytics = conn.execute("SELECT * FROM company_quarterly_analytics WHERE company_uuid = 'uuid-tcs' AND is_current = 1").fetchone()
        self.assertIsNotNone(tcs_analytics)
        self.assertEqual(tcs_analytics["fii_delta"], 3.0)
        self.assertEqual(tcs_analytics["pledged_delta"], -2.0)

        infy_analytics = conn.execute("SELECT * FROM company_quarterly_analytics WHERE company_uuid = 'uuid-infy' AND is_current = 1").fetchone()
        self.assertEqual(infy_analytics["promoter_delta"], 5.0)

        rel_analytics = conn.execute("SELECT * FROM company_quarterly_analytics WHERE company_uuid = 'uuid-reliance' AND is_current = 1").fetchone()
        self.assertEqual(rel_analytics["mf_delta"], 4.0)

        # 4. Verify rankings report output
        report_row = conn.execute("SELECT * FROM analytics_reports WHERE quarter = '2026-Q2' AND is_current = 1").fetchone()
        self.assertIsNotNone(report_row)
        report_data = json.loads(report_row["report_data"])

        # Check Rankings sorting
        self.assertEqual(report_data["rankings"]["fii_buying"][0]["symbol"], "TCS")
        self.assertEqual(report_data["rankings"]["promoter_buying"][0]["symbol"], "INFY")
        self.assertEqual(report_data["rankings"]["mf_buying"][0]["symbol"], "RELIANCE")
        self.assertEqual(report_data["rankings"]["pledge_reduction"][0]["symbol"], "TCS")
        self.assertEqual(report_data["rankings"]["pledge_reduction"][0]["delta"], -2.0)

        # Check Combined Rankings sorting (INFY with promoter 5.0% > TCS with promoter 0.0%/FII 3.0% > RELIANCE with promoter 0.0%/FII 0.0%)
        combined = report_data["rankings"]["combined"]
        self.assertEqual(len(combined), 3)
        self.assertEqual(combined[0]["symbol"], "INFY")
        self.assertEqual(combined[1]["symbol"], "TCS")
        self.assertEqual(combined[2]["symbol"], "RELIANCE")
        conn.close()

    def test_version_archiving(self):
        # Seed snapshots
        self._seed_snapshot("uuid-tcs", "2026-Q1", 72.0, 12.0, 10.0, 6.0, 6.0, 0.0)
        self._seed_snapshot("uuid-tcs", "2026-Q2", 72.0, 15.0, 10.0, 6.0, 3.0, 0.0)

        # First run
        self.agent.run(self.execution_id, {"execution_id": self.execution_id})

        # Second run
        other_execution_id = str(uuid.uuid4())
        with transaction() as conn:
            conn.execute(
                "INSERT INTO pipeline_executions (execution_id, status, started_at) VALUES (?, 'Running', ?)",
                (other_execution_id, datetime.now(timezone.utc).isoformat())
            )

        self.agent.run(other_execution_id, {"execution_id": other_execution_id})

        conn = get_connection()
        analytics = conn.execute(
            "SELECT is_current, execution_id FROM company_quarterly_analytics WHERE company_uuid = 'uuid-tcs' ORDER BY id"
        ).fetchall()
        self.assertEqual(len(analytics), 2)
        # Old run deprecated
        self.assertEqual(analytics[0]["is_current"], 0)
        self.assertEqual(analytics[0]["execution_id"], self.execution_id)
        # New run active
        self.assertEqual(analytics[1]["is_current"], 1)
        self.assertEqual(analytics[1]["execution_id"], other_execution_id)
        conn.close()

if __name__ == "__main__":
    unittest.main()
