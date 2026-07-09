import os
import unittest
import uuid
from datetime import datetime, timezone

from config.settings import settings
from db.connection import init_db, get_connection, transaction
from agents.trade_tracker import TradeTracker
from agents.telegram_agent import TelegramAgent
from agents.base import AgentValidationError

TEST_DB_PATH = os.path.join(os.path.dirname(__file__), "test_telegram_automation.db")
settings.db_path = TEST_DB_PATH

from unittest.mock import patch, MagicMock

class TestTelegramAutomation(unittest.TestCase):
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
        self.tracker = TradeTracker()
        self.tg_agent = TelegramAgent()

        # Seed pipeline execution and a test company
        with transaction() as conn:
            conn.execute(
                "INSERT INTO pipeline_executions (execution_id, status, started_at) VALUES (?, 'Running', ?)",
                (self.execution_id, datetime.now(timezone.utc).isoformat())
            )
            # Create a company TCS
            conn.execute(
                """
                INSERT INTO companies (company_uuid, symbol, isin, name, exchange, status, last_updated, source)
                VALUES ('uuid-tcs', 'TCS', 'INE467B01029', 'Tata Consultancy Services', 'NSE', 'Active', 'now', 'seed')
                """
            )
        
        # Isolate from live environment variables to force mock fallback during tests
        self.old_token = os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        self.old_chat_id = os.environ.pop("TELEGRAM_CHAT_ID", None)

    def tearDown(self):
        # Restore environment variables
        if self.old_token is not None:
            os.environ["TELEGRAM_BOT_TOKEN"] = self.old_token
        if self.old_chat_id is not None:
            os.environ["TELEGRAM_CHAT_ID"] = self.old_chat_id

        # Remove generated test outbox logs and DB
        db_dir = os.path.dirname(TEST_DB_PATH)
        comm_dir = os.path.join(os.path.dirname(db_dir), "communication")
        if os.path.exists(comm_dir):
            for f in os.listdir(comm_dir):
                if f.startswith("telegram_mock_outbox"):
                    try:
                        os.remove(os.path.join(comm_dir, f))
                    except OSError:
                        pass
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except OSError:
                pass

    def test_validation(self):
        with self.assertRaises(AgentValidationError):
            self.tg_agent.validate_inputs({})
        with self.assertRaises(AgentValidationError):
            self.tracker.validate_inputs({})

    @patch("yfinance.Ticker")
    def test_entry_signals(self, mock_ticker):
        mock_instance = MagicMock()
        mock_instance.fast_info = {"lastPrice": 100.0}
        mock_ticker.return_value = mock_instance

        # Insert a company with >= 28.0% Promoter Delta
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO company_quarterly_analytics (company_uuid, quarter, promoter_delta, fii_delta, dii_delta, created_at, execution_id, source)
                VALUES ('uuid-tcs', '2026-Q2', 29.50, -5.0, -10.0, 'now', ?, 'test')
                """, (self.execution_id,)
            )
            
        inputs = {
            "execution_id": self.execution_id,
            "pipeline_state": {
                "initial_inputs": {
                    "quarter": "2026-Q2"
                }
            }
        }
        
        job_id = str(uuid.uuid4())
        with transaction() as conn:
            conn.execute(
                "INSERT INTO job_executions (job_id, execution_id, agent_name, status, started_at) VALUES (?, ?, 'TradeTracker', 'Running', ?)",
                (job_id, self.execution_id, datetime.now(timezone.utc).isoformat())
            )
        result = self.tracker.execute(inputs, job_id)
        
        self.assertEqual(result["entries_opened"], 1)
        self.assertEqual(result["alerts_sent"], 1)
        
        # Verify active_trades table entry
        with get_connection() as conn:
            cursor = conn.execute("SELECT * FROM active_trades WHERE symbol = 'TCS'")
            trade = dict(cursor.fetchone())
            self.assertEqual(trade["status"], "Open")
            self.assertEqual(trade["entry_quarter"], "2026-Q2")
            self.assertEqual(trade["entry_price"], 100.0)
            self.assertEqual(trade["target_price_10"], 110.0)
            self.assertEqual(trade["target_price_20"], 120.0)
            
        # Verify mock outbox file creation
        db_dir = os.path.dirname(TEST_DB_PATH)
        comm_dir = os.path.join(os.path.dirname(db_dir), "communication")
        outbox_file = os.path.join(comm_dir, "telegram_mock_outbox.log")
        self.assertTrue(os.path.exists(outbox_file))
        
        with open(outbox_file, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("NEW ENTRY SIGNAL", content)
            self.assertIn("TCS", content)
            self.assertIn("Promoter Delta: +29.50%", content)

    def test_exit_on_promoter_selling(self):
        # 1. Insert open trade for TCS
        timestamp_now = datetime.now(timezone.utc).isoformat()
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO active_trades (
                    trade_id, symbol, entry_quarter, entry_price, current_price,
                    target_price_10, target_price_20, status, entry_date, created_at, execution_id
                ) VALUES ('trade-tcs', 'TCS', '2026-Q1', 100.0, 100.0, 110.0, 120.0, 'Open', ?, ?, ?)
                """, (timestamp_now, timestamp_now, self.execution_id)
            )
            
            # 2. Insert analytics for Q2 with negative promoter delta (promoter selling)
            conn.execute(
                """
                INSERT INTO company_quarterly_analytics (company_uuid, quarter, promoter_delta, fii_delta, dii_delta, created_at, execution_id, source)
                VALUES ('uuid-tcs', '2026-Q2', -1.2, 0.0, 0.0, 'now', ?, 'test')
                """, (self.execution_id,)
            )

        inputs = {
            "execution_id": self.execution_id,
            "pipeline_state": {
                "initial_inputs": {
                    "quarter": "2026-Q2"
                }
            }
        }
        
        job_id = str(uuid.uuid4())
        with transaction() as conn:
            conn.execute(
                "INSERT INTO job_executions (job_id, execution_id, agent_name, status, started_at) VALUES (?, ?, 'TradeTracker', 'Running', ?)",
                (job_id, self.execution_id, datetime.now(timezone.utc).isoformat())
            )
        result = self.tracker.execute(inputs, job_id)
        
        self.assertEqual(result["exits_closed"], 1)
        self.assertEqual(result["alerts_sent"], 1)
        
        # Verify active_trades closed status
        with get_connection() as conn:
            cursor = conn.execute("SELECT * FROM active_trades WHERE symbol = 'TCS'")
            trade = dict(cursor.fetchone())
            self.assertEqual(trade["status"], "Closed")
            self.assertEqual(trade["exit_reason"], "Promoter Sell")
            self.assertEqual(trade["exit_price"], 95.0)  # discount price
            
        # Verify mock outbox file contains the exit notification
        db_dir = os.path.dirname(TEST_DB_PATH)
        comm_dir = os.path.join(os.path.dirname(db_dir), "communication")
        outbox_file = os.path.join(comm_dir, "telegram_mock_outbox.log")
        with open(outbox_file, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("EXIT SIGNAL DETECTED", content)
            self.assertIn("Promoter Selling shares (-1.20%)", content)
            self.assertIn("Return: -5.00%", content)

    def test_exit_on_target_or_time_stop(self):
        # 1. Insert open trade for TCS
        timestamp_now = datetime.now(timezone.utc).isoformat()
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO active_trades (
                    trade_id, symbol, entry_quarter, entry_price, current_price,
                    target_price_10, target_price_20, status, entry_date, created_at, execution_id
                ) VALUES ('trade-tcs', 'TCS', '2026-Q1', 100.0, 100.0, 110.0, 120.0, 'Open', ?, ?, ?)
                """, (timestamp_now, timestamp_now, self.execution_id)
            )
            
            # 2. Insert analytics for Q2 with positive promoter delta (buying)
            conn.execute(
                """
                INSERT INTO company_quarterly_analytics (company_uuid, quarter, promoter_delta, fii_delta, dii_delta, created_at, execution_id, source)
                VALUES ('uuid-tcs', '2026-Q2', 32.0, 0.0, 0.0, 'now', ?, 'test')
                """, (self.execution_id,)
            )

        inputs = {
            "execution_id": self.execution_id,
            "pipeline_state": {
                "initial_inputs": {
                    "quarter": "2026-Q2"
                }
            }
        }
        
        # We will mock _get_simulated_return to return exactly 25.0% profit (to trigger Target 20 Hit)
        original_sim_ret = self.tracker._get_simulated_return
        self.tracker._get_simulated_return = lambda s, q, pd, fd, dd: 25.0
        
        job_id = str(uuid.uuid4())
        with transaction() as conn:
            conn.execute(
                "INSERT INTO job_executions (job_id, execution_id, agent_name, status, started_at) VALUES (?, ?, 'TradeTracker', 'Running', ?)",
                (job_id, self.execution_id, datetime.now(timezone.utc).isoformat())
            )
        result = self.tracker.execute(inputs, job_id)
        
        # Restore mock
        self.tracker._get_simulated_return = original_sim_ret
        
        self.assertEqual(result["exits_closed"], 1)
        
        # Verify active_trades Closed status with Target 20 Hit
        with get_connection() as conn:
            cursor = conn.execute("SELECT * FROM active_trades WHERE symbol = 'TCS'")
            trade = dict(cursor.fetchone())
            self.assertEqual(trade["status"], "Closed")
            self.assertEqual(trade["exit_reason"], "Target 20 Hit")
            self.assertEqual(trade["exit_price"], 125.0)
            
        # Verify outbox log
        db_dir = os.path.dirname(TEST_DB_PATH)
        comm_dir = os.path.join(os.path.dirname(db_dir), "communication")
        outbox_file = os.path.join(comm_dir, "telegram_mock_outbox.log")
        with open(outbox_file, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("EXIT TARGET MET", content)
            self.assertIn("Reason: Target 20 Hit", content)
            self.assertIn("Return: +25.00%", content)
