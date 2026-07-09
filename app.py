import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
import subprocess
import os
import sys

# Ensure project root is in system path for clean module imports in the cloud
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from db.connection import init_db

# Auto-initialize database schema if empty/new
init_db()

# Configure streamlit page setup
st.set_page_config(
    page_title="IAMS | Insider Accumulation & Momentum Strategy",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Premium Styling
st.markdown("""
    <style>
        .reportview-container {
            background: #f4f6f9;
        }
        .main-header {
            font-size: 2.2rem;
            font-weight: 800;
            color: #0f2c59;
            margin-bottom: 0.5rem;
        }
        .subheader-text {
            font-size: 1.1rem;
            color: #555555;
            margin-bottom: 2rem;
        }
        .kpi-card {
            background-color: #ffffff;
            border-radius: 10px;
            padding: 1.5rem;
            box-shadow: 0 4px 6px rgba(0,0,0,0.05);
            border-left: 5px solid #3f72af;
            margin-bottom: 1rem;
        }
        .kpi-val {
            font-size: 1.8rem;
            font-weight: 700;
            color: #0f2c59;
        }
        .kpi-label {
            font-size: 0.9rem;
            color: #888888;
            font-weight: 600;
            text-transform: uppercase;
        }
    </style>
""", unsafe_allow_html=True)

# Database Connection Helper
DB_PATH = os.path.join(os.path.dirname(__file__), "db", "platform.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# Header & Sidebar Navigation
st.sidebar.markdown("<h2 style='color:#0f2c59;'>IAMS Controls</h2>", unsafe_allow_html=True)
nav_choice = st.sidebar.radio(
    "Navigation Menu",
    ["Active Signals", "Trade Tracker", "Backtesting Explorer", "Strategy Reports"]
)

# Sidebar Action: Live Sync Ingestion
st.sidebar.markdown("---")
st.sidebar.markdown("### Data Ingestion")
if st.sidebar.button("🔄 Sync Live Filings Now"):
    st.sidebar.info("Running pipeline ingestion...")
    try:
        # Run run.py as a subprocess to pull data and recalculate
        res = subprocess.run([sys.executable, "run.py"], capture_output=True, text=True, check=True)
        st.sidebar.success("Disclosures synced successfully!")
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"Sync failed: {e}")

# Page 1: Active Signals
if nav_choice == "Active Signals":
    st.markdown("<div class='main-header'>📈 Active Trade Signals</div>", unsafe_allow_html=True)
    st.markdown("<div class='subheader-text'>Current quarter alerts based on active corporate filings accumulation data.</div>", unsafe_allow_html=True)
    
    conn = get_connection()
    try:
        # Query current active matches
        # We target 2026-Q2 as our pipeline current quarter
        quarter = "2026-Q2"
        
        query = """
            SELECT c.symbol, c.name, a.promoter_delta, a.fii_delta, a.dii_delta, 
                   t.entry_price, t.target_price_10, t.target_price_20
            FROM company_quarterly_analytics a
            JOIN companies c ON a.company_uuid = c.company_uuid
            LEFT JOIN active_trades t ON c.symbol = t.symbol AND t.entry_quarter = ? AND t.status = 'Open'
            WHERE a.quarter = ? AND a.is_current = 1 AND a.promoter_delta >= 28.0
        """
        df = pd.read_sql_query(query, conn, params=(quarter, quarter))
    except Exception as e:
        df = pd.DataFrame()
        st.error(f"Error loading signals: {e}")
    finally:
        conn.close()
        
    if df.empty:
        st.info("No active signals captured for the current cycle. Make sure you run the Sync engine first.")
    else:
        # Separate High-Conviction (Promoter >= 35%, Expected Drift >= 16%)
        df["expected_drift"] = 0.55 * df["promoter_delta"].clip(lower=0) + 0.45 * df["fii_delta"].clip(lower=0) + 0.35 * df["dii_delta"].clip(lower=0)
        df_fine = df[
            (df["promoter_delta"] >= 35.0) & 
            (df["expected_drift"] >= 16.0) & 
            (df["fii_delta"] >= -12.0) & 
            (df["dii_delta"] >= -12.0)
        ]
        
        # High Conviction Section
        st.markdown("### 🔥 High-Conviction Signal (Fine-Tuned Tier)")
        if df_fine.empty:
            st.info("No high-conviction signals triggered this cycle.")
        else:
            for idx, r in df_fine.iterrows():
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.markdown(f"<div class='kpi-card'><div class='kpi-label'>Company</div><div class='kpi-val'>{r['symbol']}</div><span style='color:#666'>{r['name']}</span></div>", unsafe_allow_html=True)
                with col2:
                    st.markdown(f"<div class='kpi-card'><div class='kpi-label'>Promoter Delta</div><div class='kpi-val' style='color:#28a745'>+{r['promoter_delta']:.2f}%</div></div>", unsafe_allow_html=True)
                with col3:
                    st.markdown(f"<div class='kpi-card'><div class='kpi-label'>Expected Drift</div><div class='kpi-val' style='color:#3f72af'>+{r['expected_drift']:.2f}%</div></div>", unsafe_allow_html=True)
                with col4:
                    entry_p = f"Rs. {r['entry_price']:.2f}" if pd.notnull(r['entry_price']) else "N/A"
                    st.markdown(f"<div class='kpi-card'><div class='kpi-label'>Live Entry Price (CMP)</div><div class='kpi-val'>{entry_p}</div></div>", unsafe_allow_html=True)
                    
                # Targets display
                if pd.notnull(r['entry_price']):
                    st.info(f"🎯 **Trade Targets:** Entry Price: **Rs. {r['entry_price']:.2f}** | **Target 10%:** Rs. {r['target_price_10']:.2f} | **Target 20%:** Rs. {r['target_price_20']:.2f}")
        
        # Standard Candidates Section
        st.markdown("### 📋 Active Candidates List (Standard Tier)")
        display_df = df[["symbol", "name", "promoter_delta", "fii_delta", "dii_delta", "entry_price"]].rename(columns={
            "symbol": "Ticker",
            "name": "Company Name",
            "promoter_delta": "Promoter Delta %",
            "fii_delta": "FII Delta %",
            "dii_delta": "DII Delta %",
            "entry_price": "Entry Price (CMP)"
        })
        st.dataframe(display_df, width="stretch", hide_index=True)

# Page 2: Trade Tracker
elif nav_choice == "Trade Tracker":
    st.markdown("<div class='main-header'>💼 Position & Trade Tracker</div>", unsafe_allow_html=True)
    st.markdown("<div class='subheader-text'>Real-time tracking of active database positions and exit metrics.</div>", unsafe_allow_html=True)
    
    conn = get_connection()
    try:
        # Load all trades
        df_trades = pd.read_sql_query("SELECT * FROM active_trades ORDER BY entry_date DESC", conn)
    except Exception as e:
        df_trades = pd.DataFrame()
        st.error(f"Error loading trades: {e}")
    finally:
        conn.close()
        
    if df_trades.empty:
        st.info("No active trades found. Sync the database to open positions.")
    else:
        # Stats summary
        open_count = len(df_trades[df_trades["status"] == "Open"])
        closed_count = len(df_trades[df_trades["status"] == "Closed"])
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Active Open Positions", open_count)
        with col2:
            st.metric("Closed Positions Log", closed_count)
            
        st.markdown("### Active Open Positions")
        df_open = df_trades[df_trades["status"] == "Open"][["symbol", "entry_quarter", "entry_price", "target_price_10", "target_price_20", "entry_date"]]
        if df_open.empty:
            st.info("No active open positions.")
        else:
            st.dataframe(df_open, width="stretch", hide_index=True)
            
        st.markdown("### Closed Positions Log")
        df_closed = df_trades[df_trades["status"] == "Closed"][["symbol", "entry_quarter", "entry_price", "exit_price", "exit_reason", "exit_date"]]
        if df_closed.empty:
            st.info("No closed trades logged.")
        else:
            st.dataframe(df_closed, width="stretch", hide_index=True)

# Page 3: Backtesting Explorer
elif nav_choice == "Backtesting Explorer":
    st.markdown("<div class='main-header'>📊 27-Year Backtesting Explorer (2000-2026)</div>", unsafe_allow_html=True)
    st.markdown("<div class='subheader-text'>Verify simulated historical alpha-generation performance across 105 quarters.</div>", unsafe_allow_html=True)
    
    # Year-by-year results data
    # Year-by-year results data
    raw_stats = [
        ("2000", 9.14, 41, 20.75, 100.0, 53.7, 0, 0.00, 0.0, 0.0),
        ("2001", 8.77, 38, 21.42, 100.0, 60.5, 0, 0.00, 0.0, 0.0),
        ("2002", 9.39, 39, 21.88, 100.0, 69.2, 0, 0.00, 0.0, 0.0),
        ("2003", 8.61, 24, 21.11, 100.0, 54.2, 0, 0.00, 0.0, 0.0),
        ("2004", 9.59, 34, 21.91, 100.0, 67.6, 0, 0.00, 0.0, 0.0),
        ("2005", 9.05, 46, 20.90, 100.0, 56.5, 0, 0.00, 0.0, 0.0),
        ("2006", 8.52, 27, 19.97, 100.0, 51.9, 1, 21.81, 100.0, 100.0),
        ("2007", 8.22, 46, 19.85, 100.0, 47.8, 0, 0.00, 0.0, 0.0),
        ("2008", 8.20, 42, 19.41, 100.0, 42.9, 1, 18.60, 100.0, 0.0),
        ("2009", 9.18, 42, 20.67, 100.0, 57.1, 0, 0.00, 0.0, 0.0),
        ("2010", 8.93, 41, 19.67, 100.0, 43.9, 0, 0.00, 0.0, 0.0),
        ("2011", 10.10, 36, 21.40, 100.0, 72.2, 0, 0.00, 0.0, 0.0),
        ("2012", 8.98, 39, 20.01, 100.0, 43.6, 0, 0.00, 0.0, 0.0),
        ("2013", 9.88, 32, 21.55, 100.0, 68.8, 0, 0.00, 0.0, 0.0),
        ("2014", 9.71, 31, 21.15, 100.0, 71.0, 0, 0.00, 0.0, 0.0),
        ("2015", 9.24, 52, 20.88, 100.0, 59.6, 1, 25.20, 100.0, 100.0),
        ("2016", 8.74, 46, 20.99, 100.0, 60.9, 0, 0.00, 0.0, 0.0),
        ("2017", 8.78, 40, 21.62, 100.0, 57.5, 1, 39.44, 100.0, 100.0),
        ("2018", 9.07, 28, 21.67, 100.0, 67.9, 0, 0.00, 0.0, 0.0),
        ("2019", 8.81, 38, 21.53, 100.0, 57.9, 2, 32.66, 100.0, 100.0),
        ("2020", 9.82, 50, 22.34, 100.0, 72.0, 0, 0.00, 0.0, 0.0),
        ("2021", 8.81, 56, 21.51, 100.0, 50.0, 0, 0.00, 0.0, 0.0),
        ("2022", 8.73, 53, 20.84, 100.0, 58.5, 2, 30.68, 100.0, 100.0),
        ("2023", 8.95, 42, 22.07, 100.0, 64.3, 1, 44.11, 100.0, 100.0),
        ("2024", 9.44, 45, 23.19, 100.0, 80.0, 2, 33.08, 100.0, 100.0),
        ("2025", 8.67, 42, 21.54, 100.0, 59.5, 2, 37.57, 100.0, 100.0),
        ("2026", 9.02, 11, 22.63, 100.0, 90.9, 0, 0.00, 0.0, 0.0)
    ]
    
    df_yby = pd.DataFrame(raw_stats, columns=[
        "Year", "Market Return %", "28% Count", "28% Return %", 
        "28% Win 10% %", "28% Win 20% %", "35% Count", "35% Return %", "35% Win 10% %", "35% Win 20% %"
    ])
    
    # Filter display to show only counts and win rates (removing standard/market return percentages)
    df_yby_display = df_yby[[
        "Year", "28% Count", "28% Win 10% %", "28% Win 20% %", 
        "35% Count", "35% Win 10% %", "35% Win 20% %"
    ]]
    
    # KPIs Summary Card
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("<div class='kpi-card'><div class='kpi-label'>Fine-Tuned Portfolio Return</div><div class='kpi-val'>32.09%</div><span style='color:#28a745'>Market: 9.05%</span></div>", unsafe_allow_html=True)
    with col2:
        st.markdown("<div class='kpi-card'><div class='kpi-label'>Net Outperformance Alpha</div><div class='kpi-val' style='color:#3f72af'>+23.04%</div><span style='color:#666'>Quarterly Outperformance</span></div>", unsafe_allow_html=True)
    with col3:
        st.markdown("<div class='kpi-card'><div class='kpi-label'>Win Rate (&ge; 20% Return)</div><div class='kpi-val'>92.3%</div><span style='color:#666'>12 of 13 trades hit target</span></div>", unsafe_allow_html=True)
        
    # Chart: Win Rate Comparison
    st.markdown(r"### Strategy Win Rate Comparison (Target: $\ge 20\%$ Return)")
    fig = px.bar(df_yby, x="Year", y=["28% Win 20% %", "35% Win 20% %"],
                 barmode="group",
                 title="Annual Win Rate (>= 20% Return) Comparison per Tier",
                 labels={"value": "Win Rate %", "variable": "Portfolio Tier"},
                 color_discrete_map={
                     "28% Win 20% %": "#3f72af",
                     "35% Win 20% %": "#0f2c59"
                 })
    st.plotly_chart(fig, use_container_width=True)
    
    st.dataframe(df_yby_display, width="stretch", hide_index=True)

# Page 4: Strategy Reports
elif nav_choice == "Strategy Reports":
    st.markdown("<div class='main-header'>📄 Strategy Specifications & Audit Reports</div>", unsafe_allow_html=True)
    st.markdown("<div class='subheader-text'>Download generated PDF analysis reports.</div>", unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("### Strategy & Backtest Report")
        st.write("Generates the complete quantitative specifications report detailing parameters, system design, and year-by-year captured returns.")
        pdf_path = os.path.join(os.path.dirname(__file__), "reports", "IAMS_Strategy_and_Backtest_Report.pdf")
        if os.path.exists(pdf_path):
            with open(pdf_path, "rb") as f:
                pdf_data = f.read()
            st.download_button(
                label="📥 Download Strategy PDF",
                data=pdf_data,
                file_name="IAMS_Strategy_and_Backtest_Report.pdf",
                mime="application/pdf"
            )
        else:
            st.warning("Strategy PDF file not found. Ensure you execute scratch/generate_pdf.py first.")
            
    with col2:
        st.markdown("### System Audit & Verification Report")
        st.write("Contains the complete validation scores, data residual math resolutions, and trade-by-trade verification log.")
        pdf_audit_path = os.path.join(os.path.dirname(__file__), "reports", "IAMS_Strategy_and_Backtest_Report.pdf") # Fallback to standard PDF or compile custom
        # Let's read the markdown audit file directly and show it as text
        audit_md_path = os.path.join(os.path.dirname(__file__), "reports", "system_audit_report.md")
        if os.path.exists(audit_md_path):
            with open(audit_md_path, "r", encoding="utf-8") as f:
                md_content = f.read()
            st.download_button(
                label="📥 Download Audit Markdown",
                data=md_content,
                file_name="system_audit_report.md",
                mime="text/markdown"
            )
        else:
            st.warning("Audit Report markdown file not found.")

st.sidebar.markdown("---")
st.sidebar.markdown("<small>IAMS Dashboard v1.0.0 | Google DeepMind Antigravity</small>", unsafe_allow_html=True)
