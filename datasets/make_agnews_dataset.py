"""Build setup C's dataset directory for DAB agnews.

Copies the Tapstate `article` view into agnews_state, dumps it, and writes
query_agnews_consolidated/. The article categories come from an ingest-time classification by a
language model (classify_agnews.py), an enrichment outside Tapstate; the description says so
and says how each document records it. No DAB hint text, no per-query guidance.

Usage: python make_agnews_dataset.py <dab-root>
"""
import sys
from pathlib import Path

from make_small_datasets import build

DATASETS = {
    "agnews": ("agnews_state", ["article"], """You are working with one database, agnews_state, stored in MongoDB.

agnews_state holds the consolidated state of a news archive, maintained continuously from the
archive's article, author and publication databases and from a classification table.

Guarantees:
- One document per article, identified by `article_id`. Each article embeds its publication
  metadata, and the metadata embeds the article's author.
- Every article carries one topic category, one of World, Sports, Business or
  Science/Technology. The categories were assigned at ingest by a language model that read the
  article's title and description; `classification.method` names the model. They are a
  classification, not a label written by the publisher, so a small share may be wrong.
- Publication dates are ISO (YYYY-MM-DD); `publication_year` is the year as a number.

Collections in agnews_state:
- article: one document per article
  Fields: article_id, title, description
  - metadata (object): article_id, author_id, region, publication_date, publication_year
    - author (object): author_id, name
  - classification (object): article_id, category, method
"""),
}


if __name__ == "__main__":
    build(Path(sys.argv[1]), DATASETS)
