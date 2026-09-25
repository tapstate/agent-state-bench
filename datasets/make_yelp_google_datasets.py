"""Build setup C's dataset directories for DAB yelp and googlelocal.

Copies the Tapstate views into one MongoDB database per dataset, dumps it, and writes
query_<dataset>_consolidated/ (db_config.yaml, db_description.txt, a copy of every query
directory). Descriptions state the layer's guarantees and fields only: no DAB hint text, no
per-query guidance.

Usage: python make_yelp_google_datasets.py <dab-root>
"""
import sys
from pathlib import Path

from make_small_datasets import build

DATASETS = {
    "yelp": ("yelp_state", ["business", "user", "checkin"], """You are working with one database, yelp_state, stored in MongoDB.

yelp_state holds the consolidated state of a local-business review site, maintained continuously
from the site's business, review, tip, check-in and user databases.

Guarantees:
- One document per business, identified by `business_id`. Each business embeds all of its
  reviews and tips; `business_id` is the same in every collection.
- Facts the sources keep inside free text are extracted into fields: street address, city,
  state (two-letter code) and state_name, and categories (matched against Yelp's published
  category list). A field is null when the source does not state it.
- Attribute values are typed: booleans, numbers, strings, and nested objects (for example
  BusinessParking) instead of text. Dates and times are ISO (YYYY-MM-DD, YYYY-MM-DDTHH:MM).

Collections in yelp_state:
- business: one document per business
  Fields: business_id, name, address, city, state, state_name, categories (array), review_count,
  is_open (boolean), attributes (object), hours (object: weekday -> "HH:MM-HH:MM"), description
  - reviews: array of this business's reviews
    Fields: review_id, user_id, business_id, rating (1-5), useful, funny, cool, text, date, time
  - tips: array of this business's tips
    Fields: row_id, user_id, business_id, text, date, time, compliment_count
- user: one document per user
  Fields: user_id, name, review_count, yelping_since (date the user joined), useful, funny, cool,
  elite (array of years)
- checkin: one document per business
  Fields: business_id, times (array of check-in times)
"""),
    "googlelocal": ("googlelocal_state", ["place"], """You are working with one database, googlelocal_state, stored in MongoDB.

googlelocal_state holds the consolidated state of a local-business directory and its customer
reviews, maintained continuously from the directory's business and review databases.

Guarantees:
- One document per business, identified by `gmap_id`. Each business embeds all of its reviews.
- Facts the sources keep inside free text are extracted into fields: city, state (two-letter
  code) and zip. A field is null when the source does not state it.
- Opening hours are structured per weekday as 24-hour times; review dates are ISO
  (YYYY-MM-DD, YYYY-MM-DDTHH:MM).

Collections in googlelocal_state:
- place: one document per business
  Fields: gmap_id, name, description, city, state, zip, num_of_reviews,
  hours (object: weekday -> {open: "HH:MM", close: "HH:MM", text} or {closed: true}),
  misc (object of service options, amenities and the like, as received), status_text (as received)
  - reviews: array of this business's reviews
    Fields: row_id, gmap_id, author, rating (1-5), text, review_date, review_time
"""),
}


if __name__ == "__main__":
    build(Path(sys.argv[1]), DATASETS)
