import click
from app import db
from app.models import User, Role, Publication, NewsSource, CandidateArticle


def register_commands(app):
    @app.cli.command()
    @click.option('--username', default='admin', help='Admin username')
    @click.option('--email', default='admin@example.com', help='Admin email')
    @click.option('--password', default='admin123', help='Admin password')
    def create_admin(username, email, password):
        """Create an admin user."""
        # Create admin role if it doesn't exist
        admin_role = Role.query.filter_by(name='admin').first()
        if not admin_role:
            admin_role = Role(name='admin', description='Administrator with full access')
            db.session.add(admin_role)
            click.echo('Created admin role')

        # Create editor role if it doesn't exist
        editor_role = Role.query.filter_by(name='editor').first()
        if not editor_role:
            editor_role = Role(name='editor', description='Editor can manage content')
            db.session.add(editor_role)
            click.echo('Created editor role')

        db.session.commit()

        # Check if user already exists
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            click.echo(f'User "{username}" already exists!')
            return

        # Create admin user
        admin_user = User(username=username, email=email)
        admin_user.set_password(password)
        admin_user.roles.append(admin_role)

        db.session.add(admin_user)
        db.session.commit()

        click.echo(f'Admin user created successfully!')
        click.echo(f'Username: {username}')
        click.echo(f'Email: {email}')
        click.echo(f'Password: {password}')
        click.echo('\nPlease change the password after first login!')

    @app.cli.command()
    def init_db():
        """Initialize the database with roles."""
        # Create admin role
        admin_role = Role.query.filter_by(name='admin').first()
        if not admin_role:
            admin_role = Role(name='admin', description='Administrator with full access')
            db.session.add(admin_role)
            click.echo('Created admin role')

        # Create editor role
        editor_role = Role.query.filter_by(name='editor').first()
        if not editor_role:
            editor_role = Role(name='editor', description='Editor can manage content')
            db.session.add(editor_role)
            click.echo('Created editor role')

        db.session.commit()
        click.echo('Database initialized with roles!')

    @app.cli.command()
    @click.argument('username')
    @click.option('--password', prompt=True, hide_input=True, confirmation_prompt=True, help='New password')
    def reset_password(username, password):
        """Reset a user's password."""
        user = User.query.filter_by(username=username).first()
        if not user:
            click.echo(f'User "{username}" not found!')
            return

        user.set_password(password)
        db.session.commit()
        click.echo(f'Password reset successfully for user "{username}"!')

    @app.cli.command('test-triage')
    @click.argument('publication_id', type=int)
    @click.option('--save/--no-save', default=False, help='Save triaged items as test candidates')
    @click.option('--cleanup', is_flag=True, help='Delete previously saved test-triage candidates')
    @click.option('--count', default=2, help='Number of items to sample per source type')
    @click.option('--live', is_flag=True, help='Scrape live items from sources (instead of sampling DB)')
    def test_triage(publication_id, save, cleanup, count, live):
        """Test LLM triage with sample items per source type.

        By default samples existing candidates from the DB. Use --live to
        actually run scrapers and get fresh items (hits external APIs).

        Examples:

          flask test-triage 1            # sample from DB
          flask test-triage 1 --live     # scrape live items, then triage
          flask test-triage 1 --save     # save results as test candidates
          flask test-triage 1 --cleanup  # delete test candidates from previous runs
        """
        from app.research.scrapers import DiscoveredItem, get_scraper
        from app.research.triage import triage_items, _SKIP_TRIAGE_SOURCE_TYPES

        publication = Publication.query.get(publication_id)
        if not publication:
            click.echo(f'Publication {publication_id} not found')
            return

        # Cleanup mode: delete test candidates and exit
        if cleanup:
            deleted = _cleanup_test_candidates(publication_id)
            click.echo(f'Deleted {deleted} test-triage candidates for publication {publication_id}')
            return

        click.echo(f'\nTesting triage for: {publication.name}')
        click.echo(f'Industry: {(publication.industry_description or "")[:100]}...')
        click.echo('')

        triageable_types = ['RSS Feed', 'News Site', 'News', 'Keyword Search',
                           'YouTube Keywords', 'Competitor']
        sample_items = []
        sample_source_types = []
        sample_source_ids = []

        if live:
            # Scrape live items from real sources
            click.echo('Scraping live items from sources...\n')
            sources = NewsSource.query.filter_by(
                publication_id=publication_id, is_active=True
            ).all()

            for source in sources:
                if source.source_type not in triageable_types:
                    continue
                if source.source_type in _SKIP_TRIAGE_SOURCE_TYPES:
                    continue

                scraper = get_scraper(source.source_type)
                if not scraper:
                    continue

                try:
                    click.echo(f'  [{source.source_type}] Scraping {source.name}...')
                    items = scraper.scrape(source)
                    if not items:
                        click.echo(f'  [{source.source_type}] No items returned')
                        continue

                    # Take up to `count` items with titles
                    picked = 0
                    for item in items:
                        if picked >= count:
                            break
                        sample_items.append(item)
                        sample_source_types.append(source.source_type)
                        sample_source_ids.append(source.id)
                        title_preview = (item.title or item.url)[:70]
                        click.echo(f'    -> {title_preview}')
                        picked += 1

                except Exception as e:
                    click.echo(f'  [{source.source_type}] Scraper error: {e}')
                    continue
        else:
            # Sample from existing candidates in DB
            for source_type in triageable_types:
                candidates = (
                    CandidateArticle.query
                    .join(NewsSource)
                    .filter(
                        CandidateArticle.publication_id == publication_id,
                        NewsSource.source_type == source_type,
                        CandidateArticle.title.isnot(None),
                    )
                    .order_by(CandidateArticle.discovered_at.desc())
                    .limit(count)
                    .all()
                )

                if not candidates:
                    click.echo(f'  [{source_type}] No existing candidates, skipping (try --live)')
                    continue

                for c in candidates:
                    item = DiscoveredItem(
                        url=c.url,
                        title=c.title,
                        snippet=c.snippet,
                        author=c.author,
                        published_date=c.published_date,
                    )
                    sample_items.append(item)
                    sample_source_types.append(source_type)
                    sample_source_ids.append(c.news_source_id)
                    click.echo(f'  [{source_type}] Sampled: {(c.title or c.url)[:70]}')

        if not sample_items:
            click.echo('\nNo items to triage. Try --live to scrape fresh items.')
            return

        click.echo(f'\nTriaging {len(sample_items)} items...\n')

        # Run triage
        verdicts = triage_items(
            items=sample_items,
            source_types=sample_source_types,
            industry_description=publication.industry_description or '',
            reader_personas=publication.reader_personas or '',
        )

        # Display results
        click.echo(f'{"#":<4} {"Verdict":<15} {"Date":<12} {"Source Type":<20} {"Title":<45} {"Reasoning"}')
        click.echo('-' * 140)

        counts = {'relevant_news': 0, 'maybe': 0, 'not_news': 0}
        for i, (v, st) in enumerate(zip(verdicts, sample_source_types)):
            verdict = v['verdict']
            counts[verdict] = counts.get(verdict, 0) + 1
            title = (sample_items[i].title or sample_items[i].url)[:43]
            reasoning = v.get('reasoning', '')[:35]
            pub_date = v.get('published_date')
            date_str = pub_date.strftime('%Y-%m-%d') if pub_date else '-'
            color = {'relevant_news': 'green', 'maybe': 'yellow', 'not_news': 'red'}.get(verdict, 'white')
            click.echo(f'{i:<4} {click.style(verdict, fg=color):<26} {date_str:<12} {st:<20} {title:<45} {reasoning}')

        click.echo('')
        click.echo(f'Summary: {click.style(str(counts.get("relevant_news", 0)), fg="green")} relevant, '
                   f'{click.style(str(counts.get("maybe", 0)), fg="yellow")} maybe, '
                   f'{click.style(str(counts.get("not_news", 0)), fg="red")} not_news')

        # Optionally save as test candidates
        if save:
            from app.research.dedup import url_hash
            saved = 0
            for i, (item, v, st, source_id) in enumerate(
                zip(sample_items, verdicts, sample_source_types, sample_source_ids)
            ):
                hash_val = url_hash(item.url + '?_triage_test=1')
                candidate = CandidateArticle(
                    publication_id=publication_id,
                    news_source_id=source_id,
                    url=item.url,
                    url_hash=hash_val,
                    title=f'[TRIAGE TEST] {item.title}',
                    snippet=item.snippet,
                    author=item.author,
                    published_date=item.published_date,
                    relevance_score=0,
                    status='rejected' if v['verdict'] == 'not_news' else 'new',
                    extra_metadata={
                        'triage_test': True,
                        'triage_verdict': v['verdict'],
                        'triage_reasoning': v.get('reasoning', ''),
                    },
                )
                db.session.add(candidate)
                saved += 1

            db.session.commit()
            click.echo(f'\nSaved {saved} test candidates (titles prefixed with [TRIAGE TEST])')
            click.echo(f'Run "flask test-triage {publication_id} --cleanup" to remove them')


def _cleanup_test_candidates(publication_id):
    """Delete test-triage candidates for a publication."""
    candidates = CandidateArticle.query.filter(
        CandidateArticle.publication_id == publication_id,
        CandidateArticle.title.like('[TRIAGE TEST]%'),
    ).all()

    count = len(candidates)
    for c in candidates:
        db.session.delete(c)
    db.session.commit()
    return count