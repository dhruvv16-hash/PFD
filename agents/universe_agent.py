import csv
import urllib.request
import io
import os
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any

from agents.base import BaseAgent, AgentValidationError
from audit.audit import log_audit, log_evidence
from db.connection import transaction, get_connection

class UniverseAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="UniverseAgent",
            version="1.0.0",
            role="Maintains the official list of active NSE-listed companies"
        )
        self.default_url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"

    def validate_inputs(self, inputs: dict):
        super().validate_inputs(inputs)
        # UniverseAgent expects pipeline_state and execution_id
        if "execution_id" not in inputs:
            raise AgentValidationError("Missing required input field: 'execution_id'")

    def _fetch_nse_equities(self, job_id: str) -> List[Dict[str, str]]:
        """Downloads the EQUITY_L.csv from NSE with Mozilla User-Agent, or falls back to local seed."""
        req = urllib.request.Request(
            self.default_url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        
        log_audit(job_id, "Fetch", "Attempting to download NSE equities list", {"url": self.default_url})
        
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                content = response.read().decode('utf-8')
            log_audit(job_id, "Fetch", "Successfully downloaded NSE equities list from exchange URL")
            return self._parse_csv_content(content)
        except Exception as e:
            logger = self._get_logger()
            logger.warning(f"Failed to fetch equities list from exchange URL ({str(e)}). Using local fallback seed CSV...")
            
            fallback_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "db", "equity_fallback.csv")
            log_audit(job_id, "Fetch", "Falling back to local seed CSV file", {"path": fallback_path})
            
            with open(fallback_path, "r", encoding="utf-8") as f:
                content = f.read()
            return self._parse_csv_content(content)

    def _get_logger(self):
        from logger.logger import get_logger
        return get_logger("UniverseAgent")

    def _parse_csv_content(self, csv_text: str) -> List[Dict[str, str]]:
        """Parses CSV text, strips spaces, and converts to list of dictionaries."""
        reader = csv.reader(io.StringIO(csv_text.strip()))
        
        # Read and clean headers
        headers = [h.strip().upper() for h in next(reader)]
        
        rows = []
        for line in reader:
            if not line:
                continue
            row = {headers[i]: line[i].strip() for i in range(min(len(headers), len(line)))}
            rows.append(row)
        return rows

    def execute(self, inputs: dict, job_id: str) -> dict:
        execution_id = inputs["execution_id"]
        logger = self._get_logger()
        
        # 1. Fetch NSE equities list
        raw_rows = self._fetch_nse_equities(job_id)
        
        # 2. Normalize and validate the records
        normalized_records = []
        seen_symbols = set()
        seen_isins = set()
        
        for row in raw_rows:
            symbol = row.get("SYMBOL", "").strip().upper()
            name = row.get("NAME OF COMPANY", "").strip()
            isin = row.get("ISIN NUMBER", "").strip().upper()
            series = row.get("SERIES", "").strip()
            listing_date_raw = row.get("DATE OF LISTING", "").strip()
            face_value_raw = row.get("FACE VALUE", "10").strip()

            if not symbol or not isin or not name:
                logger.debug(f"Skipping row with missing critical fields: {row}")
                continue
                
            # Verify feed does not contain duplicates
            if symbol in seen_symbols:
                raise AgentValidationError(f"Duplicate symbol '{symbol}' detected in equities feed.")
            if isin in seen_isins:
                raise AgentValidationError(f"Duplicate ISIN '{isin}' detected in equities feed.")
                
            seen_symbols.add(symbol)
            seen_isins.add(isin)

            # Standardize date to YYYY-MM-DD
            listing_date = None
            if listing_date_raw:
                for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y"):
                    try:
                        listing_date = datetime.strptime(listing_date_raw, fmt).strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        continue
                if not listing_date:
                    listing_date = listing_date_raw

            try:
                face_value = float(face_value_raw)
            except ValueError:
                face_value = 10.0

            normalized_records.append({
                "symbol": symbol,
                "name": name,
                "isin": isin,
                "series": series,
                "listing_date": listing_date,
                "face_value": face_value
            })

        log_audit(job_id, "Normalize", f"Normalized {len(normalized_records)} equities records.")

        # 3. Database operations
        timestamp_now = datetime.now(timezone.utc).isoformat()
        
        # Load all existing companies in the database
        db_companies = {}
        db_companies_by_symbol = {}
        with transaction() as conn:
            rows = conn.execute("SELECT * FROM companies").fetchall()
            for r in rows:
                db_rec = dict(r)
                db_companies[db_rec["isin"]] = db_rec
                if db_rec["status"] == "Active":
                    db_companies_by_symbol[db_rec["symbol"]] = db_rec

        new_listings_count = 0
        delistings_count = 0
        symbol_changes_count = 0
        name_changes_count = 0
        isin_changes_count = 0

        incoming_isins = {r["isin"] for r in normalized_records}
        processed_company_uuids = set()

        with transaction() as conn:
            # First Pass: Process existing and new listings
            for rec in normalized_records:
                isin = rec["isin"]
                symbol = rec["symbol"]
                name = rec["name"]
                
                if isin in db_companies:
                    db_rec = db_companies[isin]
                    company_uuid = db_rec["company_uuid"]
                    processed_company_uuids.add(company_uuid)
                    
                    # Check for Symbol Change
                    if db_rec["symbol"] != symbol:
                        # Log change history
                        conn.execute(
                            """
                            INSERT INTO company_history (company_uuid, field_name, old_value, new_value, change_date, reason, execution_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (company_uuid, "symbol", db_rec["symbol"], symbol, timestamp_now, "Symbol changed on exchange", execution_id)
                        )
                        log_audit(job_id, "ChangeDetection", f"Symbol change: {db_rec['symbol']} -> {symbol} for ISIN {isin}", conn=conn)
                        symbol_changes_count += 1
                        
                    # Check for Name Change
                    if db_rec["name"] != name:
                        conn.execute(
                            """
                            INSERT INTO company_history (company_uuid, field_name, old_value, new_value, change_date, reason, execution_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (company_uuid, "name", db_rec["name"], name, timestamp_now, "Company name changed", execution_id)
                        )
                        log_audit(job_id, "ChangeDetection", f"Name change: '{db_rec['name']}' -> '{name}' for ISIN {isin}", conn=conn)
                        name_changes_count += 1

                    # Update company record
                    conn.execute(
                        """
                        UPDATE companies
                        SET symbol = ?, name = ?, status = 'Active', series = ?, listing_date = ?, face_value = ?, last_updated = ?, delisting_date = NULL
                        WHERE company_uuid = ?
                        """,
                        (symbol, name, rec["series"], rec["listing_date"], rec["face_value"], timestamp_now, company_uuid)
                    )
                elif symbol in db_companies_by_symbol:
                    # ISIN change detected (symbol matches an active company but with different ISIN)
                    db_rec = db_companies_by_symbol[symbol]
                    company_uuid = db_rec["company_uuid"]
                    processed_company_uuids.add(company_uuid)
                    old_isin = db_rec["isin"]
                    
                    # Log ISIN change
                    conn.execute(
                        """
                        INSERT INTO company_history (company_uuid, field_name, old_value, new_value, change_date, reason, execution_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (company_uuid, "isin", old_isin, isin, timestamp_now, "ISIN changed on exchange (e.g. stock split)", execution_id)
                    )
                    log_audit(job_id, "ChangeDetection", f"ISIN change: {old_isin} -> {isin} for Symbol {symbol}", conn=conn)
                    isin_changes_count += 1
                    
                    # Check for Name Change
                    if db_rec["name"] != name:
                        conn.execute(
                            """
                            INSERT INTO company_history (company_uuid, field_name, old_value, new_value, change_date, reason, execution_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (company_uuid, "name", db_rec["name"], name, timestamp_now, "Company name changed", execution_id)
                        )
                        log_audit(job_id, "ChangeDetection", f"Name change: '{db_rec['name']}' -> '{name}' for Symbol {symbol}", conn=conn)
                        name_changes_count += 1

                    # Update company record
                    conn.execute(
                        """
                        UPDATE companies
                        SET isin = ?, name = ?, status = 'Active', series = ?, listing_date = ?, face_value = ?, last_updated = ?, delisting_date = NULL
                        WHERE company_uuid = ?
                        """,
                        (isin, name, rec["series"], rec["listing_date"], rec["face_value"], timestamp_now, company_uuid)
                    )
                else:
                    # New listing discovered
                    company_uuid = str(uuid.uuid4())
                    processed_company_uuids.add(company_uuid)
                    conn.execute(
                        """
                        INSERT INTO companies (company_uuid, symbol, isin, name, exchange, status, listing_date, series, face_value, last_updated, source)
                        VALUES (?, ?, ?, ?, 'NSE', 'Active', ?, ?, ?, ?, 'NSE Master')
                        """,
                        (company_uuid, symbol, isin, name, rec["listing_date"], rec["series"], rec["face_value"], timestamp_now)
                    )
                    
                    now_dt = datetime.now(timezone.utc)
                    quarter_str = f"{now_dt.year}-Q{(now_dt.month - 1) // 3 + 1}"
                    # Log audit/evidence
                    log_evidence(
                        job_id=job_id,
                        company_uuid=company_uuid,
                        field_name="listing",
                        value=f"New IPO Listing: {symbol} ({name})",
                        source="NSE Official Equities List",
                        confidence_score=1.0,
                        quarter=quarter_str,
                        conn=conn
                    )
                    new_listings_count += 1
            
            # Second Pass: Process Delistings (companies in DB but not in the incoming feed)
            for isin, db_rec in db_companies.items():
                company_uuid = db_rec["company_uuid"]
                if db_rec["status"] == "Active" and company_uuid not in processed_company_uuids:
                    released_symbol = f"{db_rec['symbol']}/DELISTED/{isin}"
                    
                    # 1. Update company record (soft delete status + rename symbol to release it)
                    conn.execute(
                        """
                        UPDATE companies
                        SET status = 'Delisted', delisting_date = ?, symbol = ?, last_updated = ?
                        WHERE company_uuid = ?
                        """,
                        (timestamp_now, released_symbol, timestamp_now, company_uuid)
                    )
                    
                    # 2. Log status change in history
                    conn.execute(
                        """
                        INSERT INTO company_history (company_uuid, field_name, old_value, new_value, change_date, reason, execution_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (company_uuid, "status", "Active", "Delisted", timestamp_now, "Not found in official active list", execution_id)
                    )
                    
                    log_audit(job_id, "Delisting", f"Company soft-delisted: {db_rec['symbol']} ({db_rec['name']})", conn=conn)
                    delistings_count += 1

            # 4. Save Versioned Snapshot metadata
            snapshot_id = str(uuid.uuid4())
            base_version = datetime.now(timezone.utc).strftime('%Y.%m.%d')
            row = conn.execute(
                "SELECT version FROM universe_snapshots WHERE version LIKE ? ORDER BY version DESC LIMIT 1",
                (f"{base_version}.%",)
            ).fetchone()
            
            if row:
                last_version = row["version"]
                try:
                    last_minor = int(last_version.split(".")[-1])
                    version_str = f"{base_version}.{last_minor + 1:02d}"
                except ValueError:
                    version_str = f"{base_version}.01"
            else:
                version_str = f"{base_version}.01"
            
            # Retrieve final active company count
            active_count = conn.execute("SELECT COUNT(*) FROM companies WHERE status = 'Active'").fetchone()[0]
            
            conn.execute(
                """
                INSERT INTO universe_snapshots (snapshot_id, version, execution_id, company_count, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (snapshot_id, version_str, execution_id, active_count, timestamp_now)
            )

        log_audit(
            job_id=job_id,
            step="Completion",
            action="Universe synchronization completed",
            metadata={
                "snapshot_version": version_str,
                "active_companies": active_count,
                "new_listings": new_listings_count,
                "delistings": delistings_count,
                "symbol_changes": symbol_changes_count,
                "name_changes": name_changes_count,
                "isin_changes": isin_changes_count
            }
        )

        return {
            "snapshot_id": snapshot_id,
            "version": version_str,
            "company_count": active_count,
            "metrics": {
                "records_processed": len(normalized_records),
                "new_listings": new_listings_count,
                "delistings": delistings_count,
                "symbol_changes": symbol_changes_count,
                "name_changes": name_changes_count,
                "isin_changes": isin_changes_count
            }
        }
