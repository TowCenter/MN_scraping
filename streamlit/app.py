#!/usr/bin/env python3
"""
Streamlit front-end to display scraped items from MongoDB.
"""

import os
from datetime import datetime, timezone, timedelta
import streamlit as st
from pymongo import MongoClient
import pandas as pd
import io
import pytz
from tzlocal import get_localzone
import json

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv(override=True)

# Configuration
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "campus_data")

# Define the start date for filtering announcements
start_date = datetime(2025, 1, 1)

def utc_to_local(utc_dt):
    """Function to convert UTC datetime to local time with robust timezone handling"""
    if utc_dt is None:
        return None
    if not isinstance(utc_dt, datetime):
        return utc_dt
    
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    
    try:
        local_tz = get_localzone()
        local_dt = utc_dt.astimezone(local_tz)
        return local_dt
    except Exception as e:
        print(f"Error converting timezone: {e}")
        return utc_dt if utc_dt.tzinfo else utc_dt.replace(tzinfo=timezone.utc)

def ensure_timezone_aware(dt):
    """Utility function to ensure datetime is timezone-aware (assumes UTC if naive)"""
    if dt is None:
        return None
    if not isinstance(dt, datetime):
        return dt
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

@st.cache_resource
def get_db():
    """Connect to MongoDB"""
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

def get_filtered_count(query):
    """Get count of documents matching query"""
    try:
        db = get_db()
        return db.articles.count_documents(query, maxTimeMS=10000)
    except Exception as e:
        print(f"Error counting documents: {e}")
        return 0

@st.cache_data(ttl=300)
def get_organizations_data(mongo_uri, db_name):
    """Get all organizations data"""
    client = MongoClient(mongo_uri)
    db = client[db_name]
    orgs_cursor = db.orgs.find({}, {"name": 1, "color": 1, "scrapers": 1})
    return list(orgs_cursor)

@st.cache_data(ttl=300)
def get_scraper_mapping(_organizations_data):
    """Create mapping from scraper path to scraper info"""
    scraper_mapping = {}

    for org in _organizations_data:
        org_name = org.get("name", "Unknown Org")
        scrapers = org.get("scrapers", [])

        for scraper in scrapers:
            path = scraper.get("path", "")
            url = scraper.get("url", "")

            if path:
                scraper_mapping[path] = {
                    "org_name": org_name,
                    "url": url,
                }

    return scraper_mapping

def get_scraper_paths_by_org(_organizations_data, org_name):
    """Get all scraper paths that belong to a specific org"""
    matching_paths = []
    for org in _organizations_data:
        if org.get("name") == org_name:
            scrapers = org.get("scrapers", [])
            for scraper in scrapers:
                path = scraper.get("path")
                if path:
                    matching_paths.append(path)
            break  # Found the org, no need to continue
    return matching_paths


def get_paginated_announcements(query_dict, page, page_size):
    """Get paginated announcements"""
    try:
        db = get_db()
        start_idx = page * page_size
        
        projection = {
            "_id": 0,
            "title": 1,
            "org": 1, 
            "date": 1,
            "scraper": 1,
            "url": 1,
            "content": 1,
            "llm_response": 1
        }
        
        cursor = db.articles.find(query_dict, projection).sort("date", -1).skip(start_idx).limit(page_size).max_time_ms(10000)
        announcements = list(cursor)
        
        for ann in announcements:
            if 'date' in ann and ann['date']:
                ann['date'] = ensure_timezone_aware(ann['date'])
        
        return announcements
    except Exception as e:
        print(f"Error fetching announcements: {e}")
        return []

def convert_to_csv(announcements, scraper_mapping):
    """Convert announcements data to CSV format"""
    processed_data = []
    
    for ann in announcements:
        processed_ann = {
            "title": ann.get("title", ""),
            "org": ann.get("org", ""),
            "date": ann.get("date"),
            "url": ann.get("url", ""),
        }
        
        scraper_path = ann.get("scraper", "")
        scraper_type = scraper_path.split(".")[-1] if scraper_path else "Unknown"

        processed_ann["announcement_type"] = scraper_type
        
        llm_response = ann.get("llm_response", {})
        
        classification_fields = [
            "government_related", "lawsuit_related", "funding_related", 
            "protest_related", "layoff_related", "trump_related"
        ]
        
        for field_name in classification_fields:
            field_data = llm_response.get(field_name, {})
            processed_ann[f"{field_name}"] = field_data.get("related", False)
            processed_ann[f"{field_name}_reason"] = field_data.get("reason", "") if field_data.get("related") else ""
        
        processed_data.append(processed_ann)
    
    df = pd.DataFrame(processed_data)
    
    if 'date' in df.columns:
        df['date'] = df['date'].apply(lambda x: utc_to_local(x).strftime('%Y-%m-%d %I:%M:%S %p') if isinstance(x, datetime) else str(x))
    
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)
    return csv_buffer.getvalue()

def display_dashboard_tab(db):
    """Comprehensive dashboard with stats and insights"""
    st.markdown("### 📊 Dashboard")
    
    organizations_data = get_organizations_data(MONGO_URI, DB_NAME)
    
    # === KEY METRICS ROW ===
    col1, col2, col3, col4 = st.columns(4)
    
    # Basic counts
    total_orgs = len(organizations_data)
    total_announcements = db.articles.count_documents({"date": {"$gte": start_date}})
    
    # Health metrics
    current_time = datetime.now(timezone.utc)
    broken_scrapers = 0
    total_scrapers = 0
    for org in organizations_data:
        for scraper in org.get("scrapers", []):
            total_scrapers += 1
            last_run = scraper.get("last_run")
            if last_run and isinstance(last_run, datetime):
                if last_run.tzinfo is None:
                    last_run = last_run.replace(tzinfo=timezone.utc)
                hours_since_run = (current_time - last_run).total_seconds() / 3600
                if hours_since_run > 25:
                    broken_scrapers += 1
            # Don't count as broken if no last_run data
    
    # Today's activity
    today = datetime.now()
    today_start = datetime(today.year, today.month, today.day)
    schools_updated_today = len(db.articles.distinct("org", {"date": {"$gte": today_start}}))
    announcements_today = db.articles.count_documents({"date": {"$gte": today_start}})
    
    with col1:
        st.metric("Total Orgs", total_orgs)
    with col2:
        st.metric("Total Items", f"{total_announcements:,}")
    with col3:
        st.metric("Orgs Active Today", f"{schools_updated_today}/{total_orgs}")
    with col4:
        health_color = "🟢" if broken_scrapers == 0 else "🔴"
        healthy_scrapers = total_scrapers - broken_scrapers
        st.metric(f"{health_color} System Health", f"{healthy_scrapers}/{total_scrapers} OK")
    
    # === RECENT ACTIVITY ===
    st.markdown("### 📈 Recent Activity")
    
    activity_col1, activity_col2 = st.columns(2)
    
    with activity_col1:
        st.markdown("**📅 Last 7 Days**")
        week_ago = datetime.now() - timedelta(days=7)
        daily_counts = []
        
        for i in range(7):
            day = week_ago + timedelta(days=i)
            day_start = datetime(day.year, day.month, day.day)
            day_end = day_start + timedelta(days=1)
            
            count = db.articles.count_documents({
                "date": {"$gte": day_start, "$lt": day_end}
            })
            daily_counts.append({
                "Date": day.strftime("%m/%d"),
                "Items": count
            })
        
        if daily_counts:
            daily_df = pd.DataFrame(daily_counts)
            st.dataframe(daily_df, hide_index=True, use_container_width=True)
    
    with activity_col2:
        st.markdown("**🏆 Most Active Orgs (30 days)**")
        month_ago = datetime.now() - timedelta(days=30)
        
        pipeline = [
            {"$match": {"date": {"$gte": month_ago}}},
            {"$group": {"_id": "$org", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ]
        
        top_schools = list(db.articles.aggregate(pipeline))
        
        if top_schools:
            schools_df = pd.DataFrame([
                {"Org": item["_id"], "Items": item["count"]}
                for item in top_schools
            ])
            st.dataframe(schools_df, hide_index=True, use_container_width=True)
        else:
            st.info("No recent activity")
    
    # === CONTENT INSIGHTS ===
    st.markdown("### 🔍 Content Categories")
    
    categories = [
        ("government_related", "Government Related", "🏛️"),
        ("lawsuit_related", "Lawsuit Related", "⚖️"), 
        ("funding_related", "Funding Related", "💰"),
        ("protest_related", "Protest Related", "📢"),
        ("layoff_related", "Layoff Related", "📉"),
        ("trump_related", "Trump Related", "🇺🇸")
    ]
    
    # Create two columns for categories
    cat_col1, cat_col2 = st.columns(2)
    
    category_data = []
    for field, display_name, emoji in categories:
        count = db.articles.count_documents({
            f"llm_response.{field}.related": True,
            "date": {"$gte": start_date}
        })
        category_data.append({
            "Category": f"{emoji} {display_name}",
            "Count": count,
            "% of Total": f"{(count/total_announcements*100):.1f}%" if total_announcements > 0 else "0%"
        })
    
    with cat_col1:
        if category_data:
            # First 3 categories
            cat_df1 = pd.DataFrame(category_data[:3])
            st.dataframe(cat_df1, hide_index=True, use_container_width=True)
    
    with cat_col2:
        if category_data:
            # Last 3 categories
            cat_df2 = pd.DataFrame(category_data[3:])
            st.dataframe(cat_df2, hide_index=True, use_container_width=True)
    


def display_system_health_tab(db):
    """Scraper health monitoring"""
    st.markdown("### 🔧 System Health")

    organizations_data = get_organizations_data(MONGO_URI, DB_NAME)

    if not organizations_data:
        st.warning("No orgs found in the database.")
        return

    # === SUMMARY METRICS from orgs config ===
    total_scrapers = 0
    passing_scrapers = 0
    error_scrapers = 0
    no_results_scrapers = 0
    inactive_scrapers = 0

    for org in organizations_data:
        for scraper in org.get("scrapers", []):
            total_scrapers += 1
            active = scraper.get("active", True)
            status = scraper.get("last_run_status")
            if active is False:
                inactive_scrapers += 1
            elif status == "pass":
                passing_scrapers += 1
            elif status == "error":
                error_scrapers += 1
            elif status == "unable_to_fetch":
                no_results_scrapers += 1

    summary_col1, summary_col2, summary_col3, summary_col4, summary_col5 = st.columns(5)
    with summary_col1:
        st.metric("Total", total_scrapers)
    with summary_col2:
        st.metric("Passing", passing_scrapers)
    with summary_col3:
        st.metric("Error", error_scrapers)
    with summary_col4:
        st.metric("No Results", no_results_scrapers)
    with summary_col5:
        st.metric("Inactive", inactive_scrapers)

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
        selected_school = st.selectbox("Filter by Org", org_options, key="health_school_filter")

    # === PER-SCHOOL EXPANDERS ===
    # Sort: error orgs first, then alphabetical
    def school_sort_key(org):
        has_error = any(
            s.get("last_run_status") in ("error", "unable_to_fetch") and s.get("active", True) is not False
            for s in org.get("scrapers", [])
        )
        return (0 if has_error else 1, org.get("name", "").lower())

    sorted_orgs = sorted(organizations_data, key=school_sort_key)

    for org in sorted_orgs:
        org_name = org.get("name", "Unknown Org")
        scrapers = org.get("scrapers", [])

        # Apply school filter
        if selected_school != "All" and org_name != selected_school:
            continue

        # Apply status filter
        if status_filter == "Error":
            has_relevant = any(
                s.get("last_run_status") == "error" and s.get("active", True) is not False
                for s in scrapers
            )
            if not has_relevant:
                continue
        elif status_filter == "No Results":
            has_relevant = any(
                s.get("last_run_status") == "unable_to_fetch" and s.get("active", True) is not False
                for s in scrapers
            )
            if not has_relevant:
                continue
        elif status_filter == "Inactive only":
            has_inactive = any(s.get("active", True) is False for s in scrapers)
            if not has_inactive:
                continue

        with st.expander(f"**{org_name}**", expanded=False):
            if not scrapers:
                st.info("No scrapers configured.")
                continue

            current_time_local = datetime.now(timezone.utc)
            rows = []
            for scraper in scrapers:
                path = scraper.get("path", "")
                module_name = path.split(".")[-1] if path else path
                status = scraper.get("last_run_status")
                active = scraper.get("active", True)
                last_run = scraper.get("last_run")
                count = scraper.get("last_run_count", 0)
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
                if isinstance(last_run, datetime):
                    lr = last_run.replace(tzinfo=timezone.utc) if last_run.tzinfo is None else last_run
                    local_dt = utc_to_local(lr)
                    if local_dt:
                        last_run_str = local_dt.strftime("%Y-%m-%d %I:%M %p")
                    hours_ago = (current_time_local - lr).total_seconds() / 3600
                    if hours_ago < 1:
                        since_str = f"{int(hours_ago * 60)}m ago"
                    elif hours_ago < 24:
                        since_str = f"{int(hours_ago)}h ago"
                    else:
                        since_str = f"{int(hours_ago / 24)}d ago"

                rows.append({
                    "Status": status_icon,
                    "Active": active_icon,
                    "Scraper": module_name,
                    "Last Run": last_run_str,
                    "Since": since_str,
                    "Count": count,
                    "URL": url,
                })

            df = pd.DataFrame(rows)
            st.dataframe(
                df,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "URL": st.column_config.LinkColumn("URL", display_text="Open"),
                    "Status": st.column_config.TextColumn("Status", width="medium"),
                    "Active": st.column_config.TextColumn("Active", width="small"),
                    "Scraper": st.column_config.TextColumn("Scraper"),
                    "Last Run": st.column_config.TextColumn("Last Run"),
                    "Since": st.column_config.TextColumn("Since", width="small"),
                    "Count": st.column_config.NumberColumn("Count", width="small"),
                }
            )

    # === CONTENT FRESHNESS ===
    st.markdown("### 📅 Content Freshness")
    current_time = datetime.now(timezone.utc)

    freshness_data = []
    for org in sorted(organizations_data, key=lambda x: x.get("name", "").lower()):
        if selected_school != "All" and org.get("name") != selected_school:
            continue
        org_name = org.get("name", "Unknown")
        try:
            latest = db.articles.find_one({"org": org_name}, sort=[("date", -1)])
        except Exception:
            latest = None

        if latest:
            latest_date = latest.get("date")
            if isinstance(latest_date, datetime):
                if latest_date.tzinfo is None:
                    latest_date = latest_date.replace(tzinfo=timezone.utc)
                days_since = (current_time - latest_date).total_seconds() / 86400
                local_dt = utc_to_local(latest_date)
                date_str = local_dt.strftime("%Y-%m-%d") if local_dt else str(latest_date)
                if days_since <= 3:
                    freshness = "🟢 Recent"
                elif days_since <= 7:
                    freshness = "🟡 Quiet"
                else:
                    freshness = "🔴 Stale"
            else:
                date_str = "Unknown"
                freshness = "❓ Unknown"
        else:
            date_str = "No posts"
            freshness = "⚫ No Posts"

        freshness_data.append({
            "Org": org_name,
            "Latest Item": date_str,
            "Freshness": freshness,
        })

    if freshness_data:
        freshness_df = pd.DataFrame(freshness_data)
        st.dataframe(freshness_df, hide_index=True, use_container_width=True)



def display_items(db):
    """Display the items view with scraper field filtering"""
    st.markdown('Please note that this is an unedited **first draft** proof-of-concept. Classifications **WILL BE** inaccurate.')

    organizations_data = get_organizations_data(MONGO_URI, DB_NAME)
    scraper_mapping = get_scraper_mapping(organizations_data)

    school_names = sorted([org["name"] for org in organizations_data])

    st.markdown('_Check any box to filter for items identified by our LLM as related to that category._')

    col1, col2, col3 = st.columns(3)

    with col1:
        show_govt_related = st.checkbox("Government Related",
            key="show_govt_related_ann",
            help="Items responding to federal government or administration actions")
        show_lawsuit_related = st.checkbox("Lawsuit Related",
            key="show_lawsuit_related_ann",
            help="Items mentioning lawsuits or legal actions")

    with col2:
        show_funding_related = st.checkbox("Funding Related", 
            key="show_funding_related_ann",
            help="Items discussing funding cuts or financial issues")
        show_protest_related = st.checkbox("Protest Related",
            key="show_protest_related_ann",
            help="Items mentioning protests or disruptions")

    with col3:
        show_layoff_related = st.checkbox("Layoff Related", 
            key="show_layoff_related_ann",
            help="Items discussing layoffs, job cuts, staff reductions, or employment terminations")
        show_trump_related = st.checkbox("Trump Related", 
            key="show_trump_related_ann",
            help="Items related to Donald Trump")
    
    search_term = st.text_input("Search content", value="", key="search_term")

    filter_col1, filter_col2 = st.columns(2)

    with filter_col1:
        org_options = ["All"] + school_names
        selected_school = st.selectbox("Filter by Org", org_options)

    selected_scraper_type = "All"

    # NEW: Filter by specific scraper path(s), but narrow options if a school is selected
    def get_scraper_url_by_path(orgs_data, path):
        for org in orgs_data:
            for scraper in org.get("scrapers", []):
                if scraper.get("path") == path:
                    return scraper.get("url", "")
        return ""

    if selected_school != "All":
        # Only show scrapers for the selected org
        school_scraper_paths = get_scraper_paths_by_org(organizations_data, selected_school)
        scraper_items = sorted(
            [
                (
                    path,
                    f"{scraper_mapping[path]['org_name']} — {path.split('.')[-1]} ({path})"
                    + (f" [{get_scraper_url_by_path(organizations_data, path)}]" if get_scraper_url_by_path(organizations_data, path) else "")
                )
                for path in school_scraper_paths if path in scraper_mapping
            ],
            key=lambda x: x[1].lower()
        )
    else:
        # Show all scrapers
        scraper_items = sorted(
            [
                (
                    path,
                    f"{info['org_name']} — {path.split('.')[-1]} ({path})"
                    + (f" [{get_scraper_url_by_path(organizations_data, path)}]" if get_scraper_url_by_path(organizations_data, path) else "")
                )
                for path, info in scraper_mapping.items()
            ],
            key=lambda x: x[1].lower()
        )

    scraper_labels = [label for _, label in scraper_items]
    label_to_path = {label: path for path, label in scraper_items}
    selected_scraper_labels = st.multiselect(
        "Filter by Scraper (path)",
        options=scraper_labels,
        key="selected_scraper_paths"
    )
    selected_scraper_paths = [label_to_path[lbl] for lbl in selected_scraper_labels]

    query = {}
    
    # Filter by org using both 'org' field AND 'scraper' field
    if selected_school != "All":
        # Get all scraper paths for this org
        school_scraper_paths = get_scraper_paths_by_org(organizations_data, selected_school)
        
        # Use $or to match either the org field OR the scraper field
        if school_scraper_paths:
            query["$and"] = [
                {
                    "$or": [
                        {"org": selected_school},
                        {"scraper": {"$in": school_scraper_paths}}
                    ]
                }
            ]
        else:
            # Fallback to just org if no scrapers found
            query["org"] = selected_school

    # Apply scraper path multiselect (intersection with other filters)
    if selected_scraper_paths:
        if "$and" in query:
            query["$and"].append({"scraper": {"$in": selected_scraper_paths}})
        else:
            query["scraper"] = {"$in": selected_scraper_paths}

    filter_conditions = []
    if show_govt_related:
        filter_conditions.append({"llm_response.government_related.related": True})
    if show_lawsuit_related:
        filter_conditions.append({"llm_response.lawsuit_related.related": True})
    if show_funding_related:
        filter_conditions.append({"llm_response.funding_related.related": True})
    if show_protest_related:
        filter_conditions.append({"llm_response.protest_related.related": True})
    if show_layoff_related:
        filter_conditions.append({"llm_response.layoff_related.related": True})
    if show_trump_related:
        filter_conditions.append({"llm_response.trump_related.related": True})

    if filter_conditions:
        # If we already have $and from filters above, append to it
        if "$and" in query:
            query["$and"].append({"$or": filter_conditions})
        else:
            query["$or"] = filter_conditions

    # Date filter
    date_filter = {
        "$gte": start_date,
        "$exists": True,
        "$ne": None
    }
    
    # Add date filter to $and if it exists, otherwise add directly
    if "$and" in query:
        query["$and"].append({"date": date_filter})
    else:
        query["date"] = date_filter

    if search_term.strip():
        # Add search filter to $and if it exists, otherwise add directly
        search_filter = {"content": {"$regex": search_term, "$options": "i"}}
        if "$and" in query:
            query["$and"].append(search_filter)
        else:
            query["content"] = search_filter

    with st.spinner("Counting results..."):
        num_announcements = get_filtered_count(query)

    st.write(f"Number of items: **{num_announcements:,}** (from {start_date.strftime('%B %d, %Y')} onwards)")
    
    PAGE_SIZE = 20
    total_pages = max((num_announcements - 1) // PAGE_SIZE + 1, 1) if num_announcements > 0 else 1
    
    # Include selected scraper paths in filter state key to reset pagination when changed
    selected_scrapers_state = "|".join(selected_scraper_labels) if selected_scraper_labels else "ALL"
    filter_state_key = f"{selected_school}_{show_govt_related}_{show_lawsuit_related}_{show_funding_related}_{show_protest_related}_{show_layoff_related}_{show_trump_related}_{search_term}_{selected_scrapers_state}"
    
    if "last_filter_state" not in st.session_state:
        st.session_state["last_filter_state"] = filter_state_key
        st.session_state["ann_page"] = 0
    elif st.session_state["last_filter_state"] != filter_state_key:
        st.session_state["ann_page"] = 0
        st.session_state["last_filter_state"] = filter_state_key
    
    if "ann_page" not in st.session_state:
        st.session_state["ann_page"] = 0
    
    st.session_state["ann_page"] = max(0, min(st.session_state["ann_page"], total_pages - 1))
    
    col_download, col_clear = st.columns([1, 3])
    
    with col_download:
        if num_announcements > 0:
            if st.button("Generate CSV"):
                with st.spinner("Generating CSV file..."):
                    all_cursor = db.articles.find(query, {"_id": 0}).sort("date", -1)
                    all_announcements = list(all_cursor)
                    csv = convert_to_csv(all_announcements, scraper_mapping)
                    st.download_button(
                        label="Download CSV",
                        data=csv,
                        file_name="data_export.csv",
                        mime="text/csv",
                    )

    with col_clear:
        if st.button("Clear All Filters"):
            for key in list(st.session_state.keys()):
                if key.startswith(('show_', 'search_term', 'ann_page', 'last_filter_state', 'selected_scraper_paths')):
                    del st.session_state[key]
            st.rerun()

    if num_announcements == 0:
        st.info("No items found matching your filters.")
        return

    with st.spinner("Loading items..."):
        paged_announcements = get_paginated_announcements(query, st.session_state["ann_page"], PAGE_SIZE)

    for ann in paged_announcements:
        title = ann.get("title", "No Title")

        date_value = ann.get("date")
        if isinstance(date_value, datetime):
            date_value = ensure_timezone_aware(date_value)
            local_date = utc_to_local(date_value)
            date_str = local_date.strftime("%Y-%m-%d %I:%M:%S %p")
        else:
            date_str = str(date_value) if date_value else "No Date"

        scraper_path = ann.get("scraper", "")
        org_name = ann.get("org", "Unknown Org")
        scraper_type_display = scraper_path.split(".")[-1] if scraper_path else "unknown"

        url = ann.get("url", "")

        st.subheader(title)
        content = ann.get("content", "")
        announcement_html = f"""
        <p style="margin-bottom: 0.5em;">
            <strong>Org:</strong> {org_name}<br>
            <strong>Type:</strong> {scraper_type_display}<br>
            <strong>Scraper:</strong> {scraper_path}<br>
            <strong>Date:</strong> {date_str}<br>
            <strong>Content Scraped:</strong> {'✅' if content else '👎'}<br>
            <strong>URL:</strong><br/> <a href="{url}">{url}</a>
        </p>
        """
        st.markdown(announcement_html, unsafe_allow_html=True)
        
        if search_term.strip() and content:
            import re
            search_pattern = re.compile(re.escape(search_term), re.IGNORECASE)
            matches = list(search_pattern.finditer(content))
            
            if matches:
                snippets = []
                for i, match in enumerate(matches):
                    start_pos = max(0, match.start() - 100)
                    end_pos = min(len(content), match.end() + 100)
                    snippet = content[start_pos:end_pos]
                    
                    if start_pos > 0:
                        snippet = "..." + snippet
                    if end_pos < len(content):
                        snippet = snippet + "..."
                    
                    highlighted_snippet = search_pattern.sub(f"<mark style='background-color: #ffeb3b; color: #000000; padding: 2px;'>{search_term}</mark>", snippet)
                    snippets.append(highlighted_snippet)
                
                match_count = len(matches)
                match_text = "match" if match_count == 1 else "matches"
                
                snippets_html = "<br/><br/>".join([f"<strong>Match {i+1}:</strong><br/><em>{snippet}</em>" for i, snippet in enumerate(snippets)])
                
                st.markdown(f"""
                <div style="background-color: rgba(255, 255, 255, 0.1); padding: 15px; border-radius: 8px; margin: 10px 0; border-left: 4px solid #ff6b6b;">
                    <strong>Search Results ({match_count} {match_text}):</strong><br/>
                    <div>{snippets_html}</div>
                </div>
                """, unsafe_allow_html=True)

        if ann.get("llm_response"):
            llm_response = ann.get("llm_response")
            categories_found = []

            if show_govt_related and llm_response.get("government_related", {}).get("related"):
                categories_found.append(("Government", llm_response["government_related"].get("reason", "")))

            if show_lawsuit_related and llm_response.get("lawsuit_related", {}).get("related"):
                categories_found.append(("Lawsuit", llm_response["lawsuit_related"].get("reason", "")))

            if show_funding_related and llm_response.get("funding_related", {}).get("related"):
                categories_found.append(("Funding", llm_response["funding_related"].get("reason", "")))

            if show_protest_related and llm_response.get("protest_related", {}).get("related"):
                categories_found.append(("Protest", llm_response["protest_related"].get("reason", "")))

            if show_layoff_related and llm_response.get("layoff_related", {}).get("related"):
                categories_found.append(("Layoffs", llm_response["layoff_related"].get("reason", "")))

            if show_trump_related and llm_response.get("trump_related", {}).get("related"):
                categories_found.append(("Trump", llm_response["trump_related"].get("reason", "")))

            for category, reason in categories_found:
                st.markdown(f"**AI Classification ({category}):** {reason}")

        st.markdown("<hr style=\"margin-top:0.5em;margin-bottom:0.5em;\">", unsafe_allow_html=True)

    if total_pages > 1:
        st.markdown("<br>", unsafe_allow_html=True)
        
        col_prev, col_page, col_next = st.columns([1,2,1])
        
        with col_prev:
            prev_disabled = st.session_state["ann_page"] == 0
            if st.button("Prev", key="ann_prev_unique", disabled=prev_disabled):
                if st.session_state["ann_page"] > 0:
                    st.session_state["ann_page"] -= 1
                    st.rerun()
        
        with col_page:
            st.markdown(f"<div style='text-align:center; padding-top:8px;'>Page <b>{st.session_state['ann_page']+1}</b> of <b>{total_pages}</b></div>", unsafe_allow_html=True)
        
        with col_next:
            next_disabled = st.session_state["ann_page"] >= total_pages - 1
            if st.button("Next", key="ann_next_unique", disabled=next_disabled):
                if st.session_state["ann_page"] < total_pages - 1:
                    st.session_state["ann_page"] += 1
                    st.rerun()

def main():
    st.set_page_config(
        page_title="Scraper Monitor [DRAFT]",
        page_icon="🔍",
        layout="wide",
        initial_sidebar_state="expanded",
        menu_items={"About": "This is a draft version of the Scraper Monitor."}
    )

    st.title("Scraper Monitor [DRAFT]")
    
    try:
        db = get_db()
        
        # Test database connection
        try:
            test_count = db.articles.count_documents({})
            print(f"Database connection successful. Total articles: {test_count}")
        except Exception as db_test_error:
            st.error(f"Database query error: {db_test_error}")
            return
        
        # Create three streamlined tabs
        tab1, tab2, tab3 = st.tabs(["📊 Dashboard", "📋 Items", "🔧 System Health"])
        
        with tab1:
            try:
                display_dashboard_tab(db)
            except Exception as e:
                st.error(f"Error in dashboard: {e}")
        
        with tab2:
            try:
                display_items(db)
            except Exception as e:
                st.error(f"Error in items: {e}")
        
        with tab3:
            try:
                display_system_health_tab(db)
            except Exception as e:
                st.error(f"Error in system health: {e}")
            
    except Exception as e:
        st.error(f"Database connection error: {e}")

if __name__ == "__main__":
    main()