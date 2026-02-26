#!/usr/bin/env python3
"""
MongoDB setup script for the `org_data` database.
Creates/enforces JSON Schema validation and indexes for:
- <content_type> (from config.json)
- <content_type>_scrapers (from config.json)
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from pymongo import MongoClient
from pymongo.errors import OperationFailure

# load environment vars from .env file
from dotenv import load_dotenv
load_dotenv(override=True)

from utils import setup_logging
from scraper_generator.generator import load_content_config

# Set up logging to logs/db.log
log_file = os.path.join(os.path.dirname(__file__), '..', 'logs', 'db.log')
logger = setup_logging('INFO', log_file)

# --- MongoDB connection setup ---
MONGO_URI = os.environ.get("MONGO_URI")
DB_NAME = os.environ.get("DB_NAME")
if not MONGO_URI or not DB_NAME:
    raise ValueError("MONGO_URI and DB_NAME must be set in the environment variables.")

# Connect to MongoDB
client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
db = client[DB_NAME]

# --- Load content config ---
content_config = load_content_config()
content_col  = content_config["content_type"]                    # e.g. "articles"
scrapers_col = content_config["content_type"] + "_scrapers"      # e.g. "articles_scrapers"
fields       = content_config["fields"]

# Find URL-type field for unique index (fallback to "url")
url_field = next((f["name"] for f in fields if f.get("type") == "url"), "url")

# --- Build dynamic content collection schema ---
def _bson_type_for_field(field):
    t = field.get("type", "text")
    if t == "date":
        return {"bsonType": ["date", "null"]}
    else:  # "text" or "url"
        return {"bsonType": "string"}

required_fields = ["org", "last_updated_at"] + [
    f["name"] for f in fields if f.get("required")
]

properties = {
    "_id":            {"bsonType": "objectId"},
    "org":            {"bsonType": "string"},
    "content":        {"bsonType": "string"},
    "last_updated_at": {"bsonType": "date"},
}
for field in fields:
    properties[field["name"]] = _bson_type_for_field(field)

content_schema = {
    "bsonType": "object",
    "required": required_fields,
    "properties": properties,
    "additionalProperties": True,
}

try:
    db.create_collection(
        content_col,
        validator={"$jsonSchema": content_schema},
        validationLevel="strict"
    )
except OperationFailure:
    db.command({
        "collMod": content_col,
        "validator": {"$jsonSchema": content_schema},
        "validationLevel": "strict"
    })
# Unique index on URL field
db[content_col].create_index(url_field, unique=True)

# --- scrapers collection schema & setup ---
# 'scrapers' is optional; if present, must be an array of objects
scrapers_schema = {
    "bsonType": "object",
    "required": ["name"],
    "properties": {
        "_id":     {"bsonType": "objectId"},
        "name":    {"bsonType": "string"},
        "scrapers": {
            "bsonType": "array",
            "items": {
                "bsonType": "object",
                "required": ["path", "url"],
                "properties": {
                    "path": {"bsonType": "string"},
                    "url":  {"bsonType": "string"}
                },
                "additionalProperties": True
            }
        },
        "last_run": {"bsonType": ["date", "null"]}
    },
    "additionalProperties": True
}
try:
    db.create_collection(
        scrapers_col,
        validator={"$jsonSchema": scrapers_schema},
        validationLevel="strict"
    )
except OperationFailure:
    db.command({
        "collMod": scrapers_col,
        "validator": {"$jsonSchema": scrapers_schema},
        "validationLevel": "strict"
    })
# Unique index on name
db[scrapers_col].create_index("name", unique=True)

logger.info(f"✅ MongoDB schema and indexes ensured for '{content_col}' and '{scrapers_col}'.")
