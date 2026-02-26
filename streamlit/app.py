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

# Define the start date for filtering announcements
start_date = datetime(2025, 1, 1)

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

def build_csv(db):
    cursor = db[CONTENT_COL].find(
        {"date": {"$gte": start_date}},
        {"_id": 0, "title": 1, "org": 1, "date": 1, "url": 1, "scraper": 1}
    ).sort("date", -1)
    rows = []
    for doc in cursor:
        d = doc.get("date")
        if isinstance(d, datetime):
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            local_dt = utc_to_local(d)
            doc["date"] = local_dt.strftime("%Y-%m-%d %I:%M:%S %p") if local_dt else str(d)
        rows.append(doc)
    df = pd.DataFrame(rows)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


def main():
    st.set_page_config(
        page_title="Scraper Monitor",
        page_icon="🔍",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    st.title("Scraper Monitor")

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
    total_orgs = len(organizations_data)
    total_items = db[CONTENT_COL].count_documents({"date": {"$gte": start_date}})

    today = datetime.now()
    today_start = datetime(today.year, today.month, today.day)
    scrapers_with_new_content = len(db[CONTENT_COL].distinct("scraper", {"date": {"$gte": today_start}}))

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

    col1, col2, _sep, col3, col4, col5, col6, col7 = st.columns([1.2, 1.2, 0.15, 1, 1, 1, 1, 1])
    with col1:
        st.metric("Total Scrapers", total_scrapers_all)
    with col2:
        st.metric("Total Items", f"{total_items:,}")
    with _sep:
        st.markdown("<div style='border-left:1px solid #444; height:60px; margin:12px auto;'></div>", unsafe_allow_html=True)
    with col3:
        st.metric("Active Today", scrapers_with_new_content)
    with col4:
        st.metric("✅ Passing", passing)
    with col5:
        st.metric("🔴 Error", errors)
    with col6:
        st.metric("🟡 No Results", no_results)
    with col7:
        st.metric("⏸️ Inactive", inactive)

    # === CSV EXPORT ===
    if st.button("Generate CSV of scraped items"):
        with st.spinner("Building CSV..."):
            csv = build_csv(db)
        st.download_button(
            label="Download CSV",
            data=csv,
            file_name="scraped_items.csv",
            mime="text/csv",
        )

    st.markdown("---")

    # === FILTERS ===
    filter_col1, filter_col2 = st.columns(2)
    with filter_col1:
        status_filter = st.radio(
            "Filter by Status",
            options=["All", "Error", "No Results", "Inactive only"],
            horizontal=True,
            key="health_status_filter"
        )
    with filter_col2:
        org_options = ["All"] + sorted([org.get("name", "Unknown") for org in organizations_data])
        selected_org = st.selectbox("Filter by Org", org_options, key="health_school_filter")

    # === FLAT SCRAPER TABLE ===
    scraper_counts = {
        doc["_id"]: doc["count"]
        for doc in db[CONTENT_COL].aggregate([
            {"$group": {"_id": "$scraper", "count": {"$sum": 1}}}
        ])
    }

    rows = []
    for org in organizations_data:
        org_name = org.get("name", "Unknown Org")

        if selected_org != "All" and org_name != selected_org:
            continue

        scrapers = org.get("scrapers", [])

        # Org-level status filter: skip org if no matching scrapers
        if status_filter == "Error":
            if not any(s.get("last_run_status") == "error" and s.get("active", True) is not False for s in scrapers):
                continue
        elif status_filter == "No Results":
            if not any(s.get("last_run_status") == "unable_to_fetch" and s.get("active", True) is not False for s in scrapers):
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
            last_run = scraper.get("last_run")
            count = scraper_counts.get(path, 0)
            url = scraper.get("url", "")

            if status == "pass":
                status_icon = "🟢 pass"
            elif status == "error":
                status_icon = "🔴 error"
            elif status == "unable_to_fetch":
                status_icon = "🟡 no results"
            else:
                status_icon = "⚪ no data"

            active_icon = "✅" if active is not False else "⏸️"

            last_run_str = ""
            since_str = ""
            has_error = status in ("error", "unable_to_fetch") and active is not False

            if isinstance(last_run, datetime):
                lr = last_run.replace(tzinfo=timezone.utc) if last_run.tzinfo is None else last_run
                local_dt = utc_to_local(lr)
                if local_dt:
                    last_run_str = local_dt.strftime("%Y-%m-%d %I:%M %p")
                hours_ago = (current_time - lr).total_seconds() / 3600
                if hours_ago < 1:
                    since_str = f"{int(hours_ago * 60)}m ago"
                elif hours_ago < 24:
                    since_str = f"{int(hours_ago)}h ago"
                else:
                    since_str = f"{int(hours_ago / 24)}d ago"

            rows.append({
                "_sort": (0 if has_error else 1, org_name.lower()),
                "Org": org_name,
                "Scraper Name": module_name,
                "Status": status_icon,
                "Active": active_icon,
                "Last Run": last_run_str,
                "Since": since_str,
                "Total Scraped Items": count,
                "URL": url,
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
                "Scraper Name": st.column_config.TextColumn("Scraper Name"),
                "Status": st.column_config.TextColumn("Status", width="medium"),
                "Active": st.column_config.TextColumn("Active", width="small"),
                "Last Run": st.column_config.TextColumn("Last Run"),
                "Since": st.column_config.TextColumn("Since", width="small"),
                "Total Scraped Items": st.column_config.NumberColumn("Total Scraped Items"),
                "URL": st.column_config.LinkColumn("URL", display_text="Open"),
            }
        )
    else:
        st.info("No scrapers match the current filters.")

if __name__ == "__main__":
    main()
