import os
import unittest
import uuid
import json
import sqlite3
from datetime import datetime, timezone
from config.settings import settings
from db.connection import init_db, get_connection, transaction
from logger.logger import setup_global_logger
from agents.universe_agent import UniverseAgent
from agents.base import AgentValidationError

# Override database path for testing
TEST_DB_PATH = os.path.join(os.path.dirname(__file__), "test_universe.db")
settings.db_path = TEST_DB_PATH

class TestUniverseAgent(unittest.TestCase):
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
            
        self.agent = UniverseAgent()
        self.execution_id = str(uuid.uuid4())
        with transaction() as conn:
            conn.execute(
                "INSERT INTO pipeline_executions (execution_id, status, started_at) VALUES (?, 'Running', ?)",
                (self.execution_id, datetime.now(timezone.utc).isoformat())
            )

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except OSError:
                pass

    def test_new_listing_and_duplicate_prevention(self):
        # 1. Feed with two valid listings
        feed_data = [
            {"SYMBOL": "RELIANCE", "NAME OF COMPANY": "Reliance Industries", "ISIN NUMBER": "INE002A01018", "SERIES": "EQ", "DATE OF LISTING": "1995-11-29", "FACE VALUE": "10"},
            {"SYMBOL": "TCS", "NAME OF COMPANY": "Tata Consultancy Services", "ISIN NUMBER": "INE467B01029", "SERIES": "EQ", "DATE OF LISTING": "2004-08-25", "FACE VALUE": "1"}
        ]
        
        # Mock _fetch_nse_equities to return our test feed
        self.agent._fetch_nse_equities = lambda job_id: feed_data
        
        outputs = self.agent.run(
            execution_id=self.execution_id,
            inputs={"execution_id": self.execution_id}
        )
        
        self.assertEqual(outputs["metrics"]["new_listings"], 2)
        self.assertEqual(outputs["company_count"], 2)

        # Check DB to verify they are active
        conn = get_connection()
        companies = conn.execute("SELECT * FROM companies WHERE status = 'Active'").fetchall()
        self.assertEqual(len(companies), 2)
        
        reliance = next(c for c in companies if c["symbol"] == "RELIANCE")
        self.assertEqual(reliance["name"], "Reliance Industries")
        self.assertEqual(reliance["isin"], "INE002A01018")
        conn.close()

        # 2. Feed containing a duplicate symbol
        dup_symbol_feed = [
            {"SYMBOL": "RELIANCE", "NAME OF COMPANY": "Reliance Industries", "ISIN NUMBER": "INE002A01018", "SERIES": "EQ"},
            {"SYMBOL": "RELIANCE", "NAME OF COMPANY": "Reliance Copy", "ISIN NUMBER": "INE002A01019", "SERIES": "EQ"}
        ]
        self.agent._fetch_nse_equities = lambda job_id: dup_symbol_feed
        other_execution_id = str(uuid.uuid4())
        with transaction() as conn:
            conn.execute(
                "INSERT INTO pipeline_executions (execution_id, status, started_at) VALUES (?, 'Running', ?)",
                (other_execution_id, datetime.now(timezone.utc).isoformat())
            )
        with self.assertRaises(AgentValidationError):
            self.agent.run(
                execution_id=other_execution_id,
                inputs={"execution_id": other_execution_id}
            )

    def test_delisting_soft_delete(self):
        # 1. First run, list RELIANCE and TCS
        feed_data_1 = [
            {"SYMBOL": "RELIANCE", "NAME OF COMPANY": "Reliance Industries", "ISIN NUMBER": "INE002A01018", "SERIES": "EQ"},
            {"SYMBOL": "TCS", "NAME OF COMPANY": "Tata Consultancy Services", "ISIN NUMBER": "INE467B01029", "SERIES": "EQ"}
        ]
        self.agent._fetch_nse_equities = lambda job_id: feed_data_1
        self.agent.run(self.execution_id, {"execution_id": self.execution_id})

        # 2. Second run, TCS is missing (delisted)
        feed_data_2 = [
            {"SYMBOL": "RELIANCE", "NAME OF COMPANY": "Reliance Industries", "ISIN NUMBER": "INE002A01018", "SERIES": "EQ"}
        ]
        self.agent._fetch_nse_equities = lambda job_id: feed_data_2
        outputs = self.agent.run(self.execution_id, {"execution_id": self.execution_id})
        
        self.assertEqual(outputs["metrics"]["delistings"], 1)
        self.assertEqual(outputs["company_count"], 1)

        # Check DB: TCS should be soft-deleted (status = 'Delisted') and symbol released
        conn = get_connection()
        tcs = conn.execute("SELECT * FROM companies WHERE isin = 'INE467B01029'").fetchone()
        self.assertEqual(tcs["status"], "Delisted")
        self.assertEqual(tcs["symbol"], "TCS/DELISTED/INE467B01029")
        self.assertIsNotNone(tcs["delisting_date"])

        # History log should show the delisting action
        history = conn.execute("SELECT * FROM company_history WHERE company_uuid = ? AND field_name = 'status'", (tcs["company_uuid"],)).fetchone()
        self.assertIsNotNone(history)
        self.assertEqual(history["old_value"], "Active")
        self.assertEqual(history["new_value"], "Delisted")
        conn.close()

    def test_symbol_and_name_changes(self):
        # 1. First run: RELIANCE with original name
        feed_data_1 = [
            {"SYMBOL": "RELIANCE", "NAME OF COMPANY": "Reliance Commercial", "ISIN NUMBER": "INE002A01018", "SERIES": "EQ"}
        ]
        self.agent._fetch_nse_equities = lambda job_id: feed_data_1
        self.agent.run(self.execution_id, {"execution_id": self.execution_id})

        # 2. Second run: Ticker changed to 'RELIANCEIND' and Name to 'Reliance Industries Ltd' (same ISIN)
        feed_data_2 = [
            {"SYMBOL": "RELIANCEIND", "NAME OF COMPANY": "Reliance Industries Ltd", "ISIN NUMBER": "INE002A01018", "SERIES": "EQ"}
        ]
        self.agent._fetch_nse_equities = lambda job_id: feed_data_2
        outputs = self.agent.run(self.execution_id, {"execution_id": self.execution_id})

        self.assertEqual(outputs["metrics"]["symbol_changes"], 1)
        self.assertEqual(outputs["metrics"]["name_changes"], 1)

        # Check DB: company has new symbol and name, and history contains tracking logs
        conn = get_connection()
        company = conn.execute("SELECT * FROM companies WHERE isin = 'INE002A01018'").fetchone()
        self.assertEqual(company["symbol"], "RELIANCEIND")
        self.assertEqual(company["name"], "Reliance Industries Ltd")

        histories = conn.execute("SELECT * FROM company_history WHERE company_uuid = ? ORDER BY field_name", (company["company_uuid"],)).fetchall()
        self.assertEqual(len(histories), 2)
        
        # Name history
        name_hist = next(h for h in histories if h["field_name"] == "name")
        self.assertEqual(name_hist["old_value"], "Reliance Commercial")
        self.assertEqual(name_hist["new_value"], "Reliance Industries Ltd")

        # Symbol history
        sym_hist = next(h for h in histories if h["field_name"] == "symbol")
        self.assertEqual(sym_hist["old_value"], "RELIANCE")
        self.assertEqual(sym_hist["new_value"], "RELIANCEIND")
        conn.close()

    def test_isin_changes(self):
        # 1. First run: RELIANCE with original ISIN
        feed_data_1 = [
            {"SYMBOL": "RELIANCE", "NAME OF COMPANY": "Reliance Industries", "ISIN NUMBER": "INE002A01018", "SERIES": "EQ"}
        ]
        self.agent._fetch_nse_equities = lambda job_id: feed_data_1
        self.agent.run(self.execution_id, {"execution_id": self.execution_id})

        # 2. Second run: Same symbol 'RELIANCE' but different ISIN 'INE002A01026'
        other_execution_id = str(uuid.uuid4())
        with transaction() as conn:
            conn.execute(
                "INSERT INTO pipeline_executions (execution_id, status, started_at) VALUES (?, 'Running', ?)",
                (other_execution_id, datetime.now(timezone.utc).isoformat())
            )

        feed_data_2 = [
            {"SYMBOL": "RELIANCE", "NAME OF COMPANY": "Reliance Industries Ltd", "ISIN NUMBER": "INE002A01026", "SERIES": "EQ"}
        ]
        self.agent._fetch_nse_equities = lambda job_id: feed_data_2
        outputs = self.agent.run(other_execution_id, {"execution_id": other_execution_id})

        self.assertEqual(outputs["metrics"]["isin_changes"], 1)

        # Check DB: company has new ISIN and history contains tracking logs
        conn = get_connection()
        company = conn.execute("SELECT * FROM companies WHERE symbol = 'RELIANCE'").fetchone()
        self.assertIsNotNone(company)
        self.assertEqual(company["isin"], "INE002A01026")
        self.assertEqual(company["name"], "Reliance Industries Ltd")

        histories = conn.execute("SELECT * FROM company_history WHERE company_uuid = ? AND field_name = 'isin'", (company["company_uuid"],)).fetchall()
        self.assertEqual(len(histories), 1)
        self.assertEqual(histories[0]["old_value"], "INE002A01018")
        self.assertEqual(histories[0]["new_value"], "INE002A01026")
        conn.close()

if __name__ == "__main__":
    unittest.main()
