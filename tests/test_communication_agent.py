import os
import csv
import json
import unittest
import uuid
from datetime import datetime, timezone
from config.settings import settings
from db.connection import init_db, get_connection, transaction
from logger.logger import setup_global_logger
from agents.communication_agent import CommunicationAgent
from agents.base import AgentValidationError

# Override database path for testing
TEST_DB_PATH = os.path.join(os.path.dirname(__file__), "test_communication.db")
settings.db_path = TEST_DB_PATH

class TestCommunicationAgent(unittest.TestCase):
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
        self.agent = CommunicationAgent()

        # Seed pipeline execution and companies
        with transaction() as conn:
            conn.execute(
                "INSERT INTO pipeline_executions (execution_id, status, started_at) VALUES (?, 'Running', ?)",
                (self.execution_id, datetime.now(timezone.utc).isoformat())
            )
            conn.execute("INSERT INTO companies (company_uuid, symbol, isin, name, exchange, status, last_updated, source) VALUES ('uuid-tcs', 'TCS', 'INE467B01029', 'Tata Consultancy Services', 'NSE', 'Active', 'now', 'seed')")
            conn.execute("INSERT INTO companies (company_uuid, symbol, isin, name, exchange, status, last_updated, source) VALUES ('uuid-infy', 'INFY', 'INE009A01021', 'Infosys Limited', 'NSE', 'Active', 'now', 'seed')")

    def tearDown(self):
        # Remove files generated under tests/communication
        db_dir = os.path.dirname(TEST_DB_PATH)
        comm_dir = os.path.join(os.path.dirname(db_dir), "communication")
        if os.path.exists(comm_dir):
            for f in os.listdir(comm_dir):
                try:
                    os.remove(os.path.join(comm_dir, f))
                except OSError:
                    pass
            try:
                os.rmdir(comm_dir)
            except OSError:
                pass

        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except OSError:
                pass

    def _seed_analytics(self, company_uuid, quarter, promoter_delta, fii_delta, dii_delta, mf_delta, pledged_delta):
        timestamp_now = datetime.now(timezone.utc).isoformat()
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO company_quarterly_analytics (
                    company_uuid, quarter, promoter_delta, fii_delta, dii_delta, mf_delta, pledged_delta,
                    is_current, created_at, execution_id, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'seed')
                """,
                (company_uuid, quarter, promoter_delta, fii_delta, dii_delta, mf_delta, pledged_delta, timestamp_now, self.execution_id)
            )

    def test_file_publication_and_alerts(self):
        # Seed analytics (TCS with sig buys/reductions, INFY with minor changes)
        self._seed_analytics("uuid-tcs", "2026-Q2", 1.5, 2.5, 0.0, 1.2, -3.0)  # sig promoter, fii, mf buy, pledged reduction
        self._seed_analytics("uuid-infy", "2026-Q2", 0.2, -0.4, 0.0, 0.1, 0.0)  # minor changes (<= 1.0%)

        # Seed consolidated rankings report
        report_data = {
            "quarter": "2026-Q2",
            "rankings": {
                "combined": [
                    {
                        "symbol": "TCS",
                        "name": "Tata Consultancy Services Limited",
                        "promoter_delta": 1.5,
                        "fii_delta": 2.5,
                        "dii_delta": 1.2,
                        "mf_delta": 1.2,
                        "pledged_delta": -3.0
                    }
                ],
                "fii_buying": [{"symbol": "TCS", "name": "Tata Consultancy Services Limited", "delta": 2.5}],
                "promoter_buying": [{"symbol": "TCS", "name": "Tata Consultancy Services Limited", "delta": 1.5}],
                "mf_buying": [{"symbol": "TCS", "name": "Tata Consultancy Services Limited", "delta": 1.2}],
                "pledge_reduction": [{"symbol": "TCS", "name": "Tata Consultancy Services Limited", "delta": -3.0}]
            }
        }
        with transaction() as conn:
            # Seed profile for TCS
            conn.execute(
                "INSERT INTO company_profiles (profile_uuid, company_uuid, version, is_current, business_description, industry, sector, website, headquarters, created_at, execution_id) VALUES ('p-tcs', 'uuid-tcs', '1', 1, 'TCS biz', 'IT Services', 'Technology', 'http://tcs.com', 'Mumbai', 'now', ?)",
                (self.execution_id,)
            )
            # Seed snapshot for TCS
            conn.execute(
                "INSERT INTO ownership_snapshots (snapshot_uuid, company_uuid, quarter, version, is_current, promoter_holding_pct, fii_holding_pct, dii_holding_pct, mf_holding_pct, public_holding_pct, pledged_pct, created_at, execution_id, source) VALUES ('s-tcs-q2', 'uuid-tcs', '2026-Q2', '1', 1, 72.0, 15.0, 10.0, 6.0, 3.0, 0.0, 'now', ?, 'seed')",
                (self.execution_id,)
            )
            conn.execute(
                "INSERT INTO analytics_reports (report_uuid, quarter, report_type, report_data, is_current, created_at, execution_id) VALUES (?, '2026-Q2', 'Consolidated Rankings', ?, 1, ?, ?)",
                (str(uuid.uuid4()), json.dumps(report_data), datetime.now(timezone.utc).isoformat(), self.execution_id)
            )

        # Run CommunicationAgent
        outputs = self.agent.run(self.execution_id, {"execution_id": self.execution_id})
        self.assertEqual(outputs["status"], "success")

        comm_dir = outputs["published_directory"]
        self.assertTrue(os.path.exists(comm_dir))
        
        # Verify rankings CSV
        csv_path = outputs["rankings_csv"]
        self.assertTrue(os.path.exists(csv_path))
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            headers = next(reader)
            self.assertEqual(headers, ["Symbol", "Company Name", "Promoter Delta %", "FII Delta %", "DII Delta %", "Mutual Fund Delta %", "Pledge Delta %"])
            
            rows = list(reader)
            self.assertEqual(len(rows), 2)
            # Row 1: INFY (sorted alphabetically)
            self.assertEqual(rows[0][0], "INFY")
            self.assertEqual(rows[0][2], "0.2")
            # Row 2: TCS
            self.assertEqual(rows[1][0], "TCS")
            self.assertEqual(rows[1][2], "1.5")

        # Verify Alerts JSON
        json_path = outputs["alerts_json"]
        self.assertTrue(os.path.exists(json_path))
        with open(json_path, "r", encoding="utf-8") as f:
            alerts = json.load(f)
            
        # TCS must be captured in Promoter Buy, FII Buy, MF Buy, and Pledge Reduction
        self.assertEqual(len(alerts["promoter_buy"]), 1)
        self.assertEqual(alerts["promoter_buy"][0]["symbol"], "TCS")
        self.assertEqual(alerts["promoter_buy"][0]["change"], 1.5)

        self.assertEqual(len(alerts["fii_buy"]), 1)
        self.assertEqual(alerts["fii_buy"][0]["symbol"], "TCS")

        self.assertEqual(len(alerts["mf_buy"]), 1)
        self.assertEqual(alerts["mf_buy"][0]["symbol"], "TCS")

        self.assertEqual(len(alerts["pledge_reduction"]), 1)
        self.assertEqual(alerts["pledge_reduction"][0]["symbol"], "TCS")
        self.assertEqual(alerts["pledge_reduction"][0]["change"], -3.0)

        # INFY must NOT be in any alert categories
        for cat in alerts.values():
            for item in cat:
                self.assertNotEqual(item["symbol"], "INFY")

        # Verify Markdown executive summary
        md_path = outputs["executive_summary_md"]
        self.assertTrue(os.path.exists(md_path))
        with open(md_path, "r", encoding="utf-8") as f:
            text = f.read()
            self.assertIn("# Executive Ownership Intelligence Report — 2026-Q2", text)
            self.assertIn("Promoter Buying Alerts:** 1", text)
            self.assertIn("Tata Consultancy Services", text)

if __name__ == "__main__":
    unittest.main()
