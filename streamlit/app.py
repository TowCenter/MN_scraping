#!/usr/bin/env python3
"""
Streamlit front-end to display scraped items from MongoDB.
"""

import os
import io
from datetime import datetime, timezone
import streamlit as st
from pymongo import MongoClient
import pandas as pd
from tzlocal import get_localzone
import json

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv(override=True)

# Configuration
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "campus_data")

# Load content config for dynamic collection names
_config_path = os.path.join(os.path.dirname(__file__), '..', 'config.json')
with open(_config_path) as _f:
    _content_config = json.load(_f)
CONTENT_COL = _content_config["content_type"]
SCRAPERS_COL = _content_config["content_type"] + "_scrapers"


def utc_to_local(utc_dt):
    if utc_dt is None:
        return None
    if not isinstance(utc_dt, datetime):
        return utc_dt
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    try:
        local_tz = get_localzone()
        return utc_dt.astimezone(local_tz)
    except Exception:
        return utc_dt

@st.cache_resource
def get_db():
    client = MongoClient(
        MONGO_URI,
        maxPoolSize=50,
        minPoolSize=5,
        maxIdleTimeMS=30000,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=5000
    )
    return client[DB_NAME]

@st.cache_data(ttl=300)
def get_organizations_data(mongo_uri, db_name):
    client = MongoClient(mongo_uri)
    db = client[db_name]
    return list(db[SCRAPERS_COL].find({}, {"name": 1, "color": 1, "scrapers": 1}))

@st.cache_data(ttl=300)
def get_scraper_summary(mongo_uri, db_name):
    """Build per-scraper summary stats via MongoDB aggregation."""
    client = MongoClient(mongo_uri)
    db = client[db_name]

    dec1 = datetime(2025, 12, 1, tzinfo=timezone.utc)
    mar31 = datetime(2026, 3, 31, 23, 59, 59, tzinfo=timezone.utc)

    pipeline = [
        {
            "$group": {
                "_id": "$scraper",
                "total": {"$sum": 1},
                "min_date": {"$min": "$date"},
                "max_date": {"$max": "$date"},
                "no_content": {
                    "$sum": {
                        "$cond": [
                            {"$or": [
                                {"$eq": ["$content", None]},
                                {"$eq": ["$content", ""]},
                                {"$not": [{"$ifNull": ["$content", False]}]},
                            ]},
                            1, 0,
                        ]
                    }
                },
                "no_date": {
                    "$sum": {
                        "$cond": [
                            {"$or": [
                                {"$eq": ["$date", None]},
                                {"$not": [{"$ifNull": ["$date", False]}]},
                            ]},
                            1, 0,
                        ]
                    }
                },
                "successful_in_range": {
                    "$sum": {
                        "$cond": [
                            {"$and": [
                                {"$ne": ["$content", None]},
                                {"$ne": ["$content", ""]},
                                {"$ifNull": ["$content", False]},
                                {"$ne": ["$date", None]},
                                {"$ifNull": ["$date", False]},
                                {"$gte": ["$date", dec1]},
                                {"$lte": ["$date", mar31]},
                            ]},
                            1, 0,
                        ]
                    }
                },
            }
        },
        {"$sort": {"_id": 1}},
    ]

    rows = []
    for doc in db[CONTENT_COL].aggregate(pipeline):
        scraper_path = doc["_id"] or ""
        min_d = doc["min_date"]
        max_d = doc["max_date"]
        rows.append({
            "_path": scraper_path,
            "Min Date": min_d.strftime("%Y-%m-%d") if isinstance(min_d, datetime) else "",
            "Max Date": max_d.strftime("%Y-%m-%d") if isinstance(max_d, datetime) else "",
            "Total Articles": doc["total"],
            "No Content": doc["no_content"],
            "No Date": doc["no_date"],
            "Successful (Dec–Mar)": doc["successful_in_range"],
        })
    return rows


@st.cache_data(ttl=300)
def build_csv(mongo_uri, db_name):
    client = MongoClient(mongo_uri)
    db = client[db_name]
    cursor = db[CONTENT_COL].find({}, {"_id": 0})
    rows = []
    for doc in cursor:
        for key, val in list(doc.items()):
            if isinstance(val, datetime):
                if val.tzinfo is None:
                    val = val.replace(tzinfo=timezone.utc)
                local_dt = utc_to_local(val)
                doc[key] = local_dt.strftime("%Y-%m-%d %I:%M:%S %p") if local_dt else str(val)
        rows.append(doc)
    df = pd.DataFrame(rows)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


def main():
    st.set_page_config(
        page_title="Scraper Factory Monitor",
        page_icon="🔍",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    st.markdown("""
    <style>
    .block-container { padding-top: 1.5rem; padding-bottom: 1rem; }
    [data-testid="stMetric"] {
        background: #f4f6f9;
        border: 1px solid #dde1e7;
        border-radius: 10px;
        padding: 0.85rem 1.1rem;
    }
    [data-testid="stMetricValue"] > div { font-size: 1.65rem; }
    [data-testid="stMetricLabel"] p { color: #5a6272; font-size: 0.78rem; }
    div[data-testid="stHorizontalBlock"] { gap: 0.6rem; }
    hr { margin: 1rem 0 !important; }
    </style>
    """, unsafe_allow_html=True)

    st.title("Scraper Factory Monitor")

    try:
        db = get_db()
        db[CONTENT_COL].count_documents({})
    except Exception as e:
        st.error(f"Database connection error: {e}")
        return

    organizations_data = get_organizations_data(MONGO_URI, DB_NAME)

    if not organizations_data:
        st.warning("No orgs found in the database.")
        return

    # === METRICS ===
    current_time = datetime.now(timezone.utc)
    total_items = db[CONTENT_COL].count_documents({})

    total_scrapers_all = 0
    passing = errors = no_results = inactive = 0
    for org in organizations_data:
        for s in org.get("scrapers", []):
            total_scrapers_all += 1
            active = s.get("active", True)
            status = s.get("last_run_status")
            if active is False:
                inactive += 1
            elif status == "pass":
                passing += 1
            elif status == "error":
                errors += 1
            elif status == "unable_to_fetch":
                no_results += 1

    rate_denom = passing + errors + no_results  # known active scrapers only

    def pct(n):
        return f" ({n * 100 // rate_denom}%)" if rate_denom else ""

    m1, m2, _gap, m3, m4 = st.columns([1, 1, 0.08, 1, 1])
    with m1:
        st.metric(
            "Total Scrapers", total_scrapers_all,
            help="All registered scrapers across every org, including inactive ones.",
        )
    with m2:
        st.metric(
            "Total Items", f"{total_items:,}",
            help="Total scraped items stored in the database across all time.",
        )
    with _gap:
        st.markdown(
            "<div style='display:flex;justify-content:center;align-items:center;height:72px'>"
            "<div style='width:1px;height:52px;background:#dde1e7'></div>"
            "</div>",
            unsafe_allow_html=True,
        )
    with m3:
        st.metric(
            "Passing", f"{passing}{pct(passing)}",
            help="Active scrapers whose last run successfully inserted new items. "
                 "Percentage is share of all active (non-inactive) scrapers.",
        )
    with m4:
        st.metric(
            "Inactive", inactive,
            help="Scrapers with active=false — permanently skipped during daily runs.",
        )

    st.divider()

    # === FILTERS + CSV EXPORT ===
    fc1, fc2 = st.columns([3, 1])
    with fc1:
        org_options = ["All"] + sorted([org.get("name", "Unknown") for org in organizations_data])
        selected_org = st.selectbox("Filter by org", org_options, key="health_school_filter")
    with fc2:
        st.markdown("<div style='padding-top:1.65rem'></div>", unsafe_allow_html=True)
        st.download_button(
            label="Export CSV",
            data=build_csv(MONGO_URI, DB_NAME),
            file_name="scraped_items.csv",
            mime="text/csv",
            use_container_width=True,
        )

    # === FLAT SCRAPER TABLE ===
    scraper_stats = get_scraper_summary(MONGO_URI, DB_NAME)
    stats_by_path = {s["_path"]: s for s in scraper_stats}

    rows = []
    for org in organizations_data:
        org_name = org.get("name", "Unknown Org")

        if selected_org != "All" and org_name != selected_org:
            continue

        scrapers = org.get("scrapers", [])

        # Org-level status filter: skip org if no matching scrapers
        if status_filter == "Error":
            if not any(
                s.get("last_run_status") == "error" and s.get("active", True) is not False
                for s in scrapers
            ):
                continue
        elif status_filter == "No Results":
            if not any(
                s.get("last_run_status") == "unable_to_fetch" and s.get("active", True) is not False
                for s in scrapers
            ):
                continue
        elif status_filter == "Inactive only":
            if not any(s.get("active", True) is False for s in scrapers):
                continue

        for scraper in scrapers:
            active = scraper.get("active", True)
            status = scraper.get("last_run_status")

            # Row-level status filter
            if status_filter == "Error" and not (status == "error" and active is not False):
                continue
            if status_filter == "No Results" and not (status == "unable_to_fetch" and active is not False):
                continue
            if status_filter == "Inactive only" and active is not False:
                continue

            path = scraper.get("path", "")
            module_name = path.split(".")[-1] if path else path
            url = scraper.get("url", "")
            has_error = status in ("error", "unable_to_fetch") and active is not False

            stats = stats_by_path.get(path, {})

            rows.append({
                "_sort": (0 if has_error else 1, org_name.lower()),
                "Org": org_name,
                "Scraper": module_name,
                "URL": url,
                "Min Date": stats.get("Min Date", ""),
                "Max Date": stats.get("Max Date", ""),
                "Total Articles": stats.get("Total Articles", 0),
                "No Content": stats.get("No Content", 0),
                "No Date": stats.get("No Date", 0),
                "Successful (Dec–Mar)": stats.get("Successful (Dec–Mar)", 0),
            })

    rows.sort(key=lambda r: r["_sort"])
    for r in rows:
        del r["_sort"]

    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Org": st.column_config.TextColumn("Org"),
                "Scraper": st.column_config.TextColumn("Scraper"),
                "URL": st.column_config.LinkColumn("URL", display_text="Open"),
                "Min Date": st.column_config.TextColumn("Min Date"),
                "Max Date": st.column_config.TextColumn("Max Date"),
                "Total Articles": st.column_config.NumberColumn("Total Articles"),
                "No Content": st.column_config.NumberColumn("No Content"),
                "No Date": st.column_config.NumberColumn("No Date"),
                "Successful (Dec–Mar)": st.column_config.NumberColumn("Successful (Dec–Mar)"),
            },
        )
    else:
        st.info("No scrapers match the current filters.")

if __name__ == "__main__":
    main()
