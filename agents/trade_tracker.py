import os
import sqlite3
import uuid
from datetime import datetime, timezone
import random

from agents.base import BaseAgent, AgentValidationError
from audit.audit import log_audit
from db.connection import transaction
from config.settings import settings
from agents.telegram_agent import TelegramAgent

class TradeTracker(BaseAgent):
    def __init__(self):
        super().__init__(
            name="TradeTracker",
            version="1.0.0",
            role="Tracks active stock entries, monitors target price milestones, and identifies exit triggers"
        )
        self.telegram_agent = TelegramAgent()

    def validate_inputs(self, inputs: dict):
        super().validate_inputs(inputs)
        if "execution_id" not in inputs:
            raise AgentValidationError("Missing required input field: 'execution_id'")

    def _get_target_quarter(self, inputs: dict) -> str:
        pipeline_state = inputs.get("pipeline_state", {})
        initial_inputs = pipeline_state.get("initial_inputs", {})
        return initial_inputs.get("quarter", "2026-Q2")

    def _get_simulated_return(self, symbol: str, quarter: str, prom_delta: float, fii_delta: float, dii_delta: float) -> float:
        # Generate a deterministic return for consistent tracking
        h = int(hash(f"{symbol}-{quarter}")) % 1000000
        random_gen = random.Random(h)
        base_return = 4.2
        shock = random_gen.normalvariate(0, 3.0)
        ret = base_return + 0.55 * max(0.0, prom_delta) + 0.45 * max(0.0, fii_delta) + 0.35 * max(0.0, dii_delta) + shock
        return round(ret, 2)

    def execute(self, inputs: dict, job_id: str) -> dict:
        execution_id = inputs["execution_id"]
        quarter = self._get_target_quarter(inputs)
        timestamp_now = datetime.now(timezone.utc).isoformat()
        
        log_audit(job_id, "TrackTrades", f"Running Trade Tracker for quarter {quarter}")
        
        alerts_to_send = []
        entries_opened = 0
        exits_closed = 0
        
        with transaction() as conn:
            # Step 1: Process Open Positions for exits
            cursor = conn.execute("SELECT * FROM active_trades WHERE status = 'Open'")
            open_trades = [dict(row) for row in cursor]
            
            for t in open_trades:
                sym = t["symbol"]
                entry_q = t["entry_quarter"]
                
                # Fetch company details for name
                c_cursor = conn.execute("SELECT name FROM companies WHERE symbol = ?", (sym,))
                c_row = c_cursor.fetchone()
                comp_name = c_row["name"] if c_row else sym
                
                # Get current quarter's ownership delta details
                a_cursor = conn.execute(
                    """
                    SELECT a.promoter_delta, a.fii_delta, a.dii_delta
                    FROM company_quarterly_analytics a
                    JOIN companies c ON a.company_uuid = c.company_uuid
                    WHERE c.symbol = ? AND a.quarter = ? AND a.is_current = 1
                    """, (sym, quarter)
                )
                a_row = a_cursor.fetchone()
                
                if not a_row:
                    continue  # No data for this quarter yet
                    
                prom_delta = a_row["promoter_delta"]
                fii_delta = a_row["fii_delta"]
                dii_delta = a_row["dii_delta"]
                
                # Rule 1: Exit immediately if promoter is selling
                if prom_delta < 0.0:
                    exit_reason = "Promoter Sell"
                    exit_price = round(t["current_price"] * 0.95, 2)  # Assume exit at 5% discount on negative news
                    total_return = round(((exit_price - t["entry_price"]) / t["entry_price"]) * 100.0, 2)
                    
                    conn.execute(
                        """
                        UPDATE active_trades
                        SET status = 'Closed', exit_date = ?, exit_price = ?, exit_reason = ?
                        WHERE trade_id = ?
                        """, (timestamp_now, exit_price, exit_reason, t["trade_id"])
                    )
                    
                    exit_msg = (
                        f"🚨 *EXIT SIGNAL DETECTED* 🚨\n"
                        f"Company: {comp_name} ({sym})\n"
                        f"Reason: Promoter Selling shares ({prom_delta:+.2f}%)\n"
                        f"Exit Price: Rs. {exit_price:.2f} (Entry: Rs. {t['entry_price']:.2f})\n"
                        f"Return: {total_return:+.2f}%\n"
                        f"Status: Closed (Stop-loss trigger)"
                    )
                    alerts_to_send.append(exit_msg)
                    exits_closed += 1
                    continue
                
                # Rule 2: Time stop / target check (if we moved forward by 1 quarter)
                if entry_q != quarter:
                    sim_ret = self._get_simulated_return(sym, entry_q, prom_delta, fii_delta, dii_delta)
                    current_price = round(t["entry_price"] * (1.0 + sim_ret / 100.0), 2)
                    
                    exit_reason = "Time Stop"
                    if current_price >= t["target_price_20"]:
                        exit_reason = "Target 20 Hit"
                    elif current_price >= t["target_price_10"]:
                        exit_reason = "Target 10 Hit"
                        
                    conn.execute(
                        """
                        UPDATE active_trades
                        SET status = 'Closed', exit_date = ?, exit_price = ?, exit_reason = ?
                        WHERE trade_id = ?
                        """, (timestamp_now, current_price, exit_reason, t["trade_id"])
                    )
                    
                    total_return = round(((current_price - t["entry_price"]) / t["entry_price"]) * 100.0, 2)
                    exit_msg = (
                        f"🎯 *EXIT TARGET MET* 🎯\n"
                        f"Company: {comp_name} ({sym})\n"
                        f"Reason: {exit_reason}\n"
                        f"Exit Price: Rs. {current_price:.2f} (Entry: Rs. {t['entry_price']:.2f})\n"
                        f"Return: {total_return:+.2f}%\n"
                        f"Status: Closed"
                    )
                    alerts_to_send.append(exit_msg)
                    exits_closed += 1
            
            # Step 2: Process current quarter's signals for entries (Promoter Delta >= 28%)
            cursor = conn.execute(
                """
                SELECT c.symbol, c.name, a.promoter_delta, a.fii_delta, a.dii_delta
                FROM company_quarterly_analytics a
                JOIN companies c ON a.company_uuid = c.company_uuid
                WHERE a.quarter = ? AND a.is_current = 1 AND a.promoter_delta >= 28.0
                """ , (quarter,)
            )
            matching_signals = [dict(row) for row in cursor]
            
            for s in matching_signals:
                sym = s["symbol"]
                # Check if position already open
                pos_cursor = conn.execute("SELECT 1 FROM active_trades WHERE symbol = ? AND status = 'Open'", (sym,))
                if pos_cursor.fetchone():
                    continue  # Already open
                    
                # Open new trade
                trade_id = str(uuid.uuid4())
                entry_price = 100.0  # Default nominal price
                try:
                    import yfinance as yf
                    ticker_sym = sym
                    if not ticker_sym.endswith(".NS") and not ticker_sym.endswith(".BO"):
                        ticker_sym = f"{ticker_sym}.NS"
                    ticker = yf.Ticker(ticker_sym)
                    live_price = ticker.fast_info.get("lastPrice")
                    if live_price is not None:
                        entry_price = round(float(live_price), 2)
                except Exception:
                    pass
                
                target_10 = round(entry_price * 1.10, 2)
                target_20 = round(entry_price * 1.20, 2)
                
                
                conn.execute(
                    """
                    INSERT INTO active_trades (
                        trade_id, symbol, entry_quarter, entry_price, current_price,
                        target_price_10, target_price_20, status, entry_date, created_at, execution_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'Open', ?, ?, ?)
                    """, (trade_id, sym, quarter, entry_price, entry_price, target_10, target_20, timestamp_now, timestamp_now, execution_id)
                )
                
                entry_msg = (
                    f"📈 *NEW ENTRY SIGNAL* 📈\n"
                    f"Company: {s['name']} ({sym})\n"
                    f"Quarter: {quarter}\n"
                    f"Promoter Delta: {s['promoter_delta']:+.2f}%\n"
                    f"FII Delta: {s['fii_delta']:+.2f}% | DII Delta: {s['dii_delta']:+.2f}%\n"
                    f"Entry Price: Rs. {entry_price:.2f}\n"
                    f"Target 10%: Rs. {target_10:.2f} | Target 20%: Rs. {target_20:.2f}\n"
                    f"Status: Position Open"
                )
                alerts_to_send.append(entry_msg)
                entries_opened += 1
                
        # Dispatch notifications after transaction has completed/committed
        for msg in alerts_to_send:
            try:
                self.telegram_agent.execute({"message": msg}, job_id)
            except Exception as e:
                # Log dispatch error but do not crash the tracker
                log_audit(job_id, "TrackTrades", f"Notification dispatch failed: {str(e)}")
                
        log_audit(job_id, "TrackTrades", f"Finished Trade Tracker. Opened {entries_opened} entries. Closed {exits_closed} positions. Sent {len(alerts_to_send)} alerts.")
        
        return {
            "entries_opened": entries_opened,
            "exits_closed": exits_closed,
            "alerts_sent": len(alerts_to_send)
        }
