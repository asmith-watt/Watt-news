#!/usr/bin/env python3
"""
Debug script to check API keys and publications
"""
from app import create_app, db
from app.models import Publication

app = create_app()

with app.app_context():
    print("=" * 70)
    print("API KEY DEBUG INFORMATION")
    print("=" * 70)
    print()

    # Get all publications
    publications = Publication.query.all()

    if not publications:
        print("⚠️  NO PUBLICATIONS FOUND IN DATABASE")
        print()
    else:
        print(f"Found {len(publications)} publication(s):\n")

        for pub in publications:
            print(f"Publication: {pub.name}")
            print(f"  ID: {pub.id}")
            print(f"  Slug: {pub.slug}")
            print(f"  Active: {pub.is_active}")
            print(f"  CMS API Key: {repr(pub.cms_api_key)}")

            if pub.cms_api_key:
                print(f"  Key Length: {len(pub.cms_api_key)} characters")
                print(f"  Key Preview: {pub.cms_api_key[:10]}...{pub.cms_api_key[-10:]}")
            else:
                print(f"  ⚠️  NO API KEY SET")

            print()

    print("=" * 70)
    print("TESTING RECOMMENDATIONS:")
    print("=" * 70)
    print()
    print("1. Make sure your publication has is_active = True")
    print("2. Make sure cms_api_key is not None or empty")
    print("3. Copy the EXACT API key from above (watch for extra spaces)")
    print("4. Use the correct publication ID from above")
    print()
    print("Example curl command:")
    if publications and publications[0].cms_api_key:
        pub = publications[0]
        print(f"""
curl -X POST http://localhost:5000/api/news \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: {pub.cms_api_key}" \\
  -d '{{
    "title": "Test Article",
    "publication_id": {pub.id}
  }}'
""")
    else:
        print("  (Generate an API key first using the admin interface)")