#!/usr/bin/env python3
"""Export all articles from MongoDB to a CSV file."""

import os
import sys
import csv
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv(override=True)

MONGO_URI = os.environ.get("MONGO_URI")
DB_NAME = os.environ.get("DB_NAME")

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
client.admin.command("ping")
db = client[DB_NAME]
collection = db["articles"]

output_path = os.path.join(os.path.dirname(__file__), '..', 'articles_export.csv')

articles = list(collection.find({}, {"_id": 0}))
if not articles:
    print("No articles found.")
    sys.exit(0)

# Get all unique keys across all articles for CSV headers
all_keys = []
for key in ["scraper", "title", "date", "url", "content", "author"]:
    if any(key in a for a in articles):
        all_keys.append(key)
# Add any remaining keys not already included
for a in articles:
    for k in a.keys():
        if k not in all_keys:
            all_keys.append(k)

with open(output_path, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction='ignore')
    writer.writeheader()
    for article in articles:
        writer.writerow(article)

print(f"Exported {len(articles)} articles to {os.path.abspath(output_path)}")
