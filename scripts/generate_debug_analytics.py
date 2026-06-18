#!/usr/bin/env python3
"""
Generate the debug-analytics epic with 300+ cards over 8 weeks.
Hierarchically organized by week with tags, priorities, dates, relationships, and archives.
- Some weeks have spikes (50+ cards)
- Archive rates vary by week (low to high)
- All cards created in backlog, then flow through lanes (backlog -> in_progress -> review -> done)
- This creates transition logs for pull rate and throughput analysis
"""

import sys
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add parent to path to import board modules
sys.path.insert(0, str(Path(__file__).parent))

from board import operations
from board import epics as epics_module
from board.storage import sqlite_cache, markdown_store, event_log


def get_today():
    """Get today's date in UTC."""
    return datetime.now(timezone.utc)


def date_to_ymd(dt):
    """Convert datetime to YYYY-MM-DD format."""
    return dt.strftime('%Y-%m-%d')


def date_to_iso(dt):
    """Convert datetime to ISO format string."""
    return dt.isoformat()


def move_card_backdated(card_id: str, target_lane: str, backdated_time: datetime) -> bool:
    """Move a card to a target lane with backdated timestamp."""
    try:
        card = markdown_store.read_card(card_id)
        if not card:
            return False
        old_lane = card.lane
        card.lane = target_lane
        card.updated_at = date_to_iso(backdated_time)
        markdown_store.move_card_file(card, old_lane)
        sqlite_cache.upsert_card(card)
        event_log.record_transition(card.id, old_lane, target_lane, at=card.updated_at)
        return True
    except Exception as e:
        return False


def create_debug_analytics_epic():
    """Create the debug-analytics epic with 300+ cards over 8 weeks."""
    
    # Initialize database
    sqlite_cache.init_db()
    epics_module.ensure_epics_file()
    
    today = get_today()
    
    # Calculate 8 weeks PRIOR to today (not future)
    # 8 weeks = 56 days back from today
    start_date = today - timedelta(days=56)
    
    print(f"📅 Historical data period: {start_date.strftime('%Y-%m-%d')} to {today.strftime('%Y-%m-%d')}")
    
    # Create the epic
    epic_name = "debug-analytics"
    epics_module.create_epic(epic_name)
    print(f"✓ Created epic: {epic_name}")
    
    # Card distribution: Some weeks spike, some are lower
    WEEKS = 8
    WEEK_CARD_COUNTS = {
        1: 35,   # Foundation - normal (Week 1: ~4 weeks ago)
        2: 50,   # SPIKE: Data Collection boom
        3: 38,   # Content Processing - normal
        4: 50,   # SPIKE: AI Recommendations integration
        5: 42,   # UI Development - moderate
        6: 48,   # SPIKE: API Development boom
        7: 38,   # Testing & QA - normal (Week 7: ~1 week ago)
        8: 55,   # SPIKE: Final deployment push (Week 8: this past week)
    }
    
    # Archive rates per week (0.0 = none, 1.0 = all archived)
    ARCHIVE_RATES = {
        1: 0.10,  # Week 1: Low - just getting started
        2: 0.20,  # Week 2: Moderate - some things completing
        3: 0.40,  # Week 3: HIGH - content processing done
        4: 0.22,  # Week 4: Moderate - some features complete
        5: 0.45,  # Week 5: HIGH - UI mostly done
        6: 0.15,  # Week 6: Low - API work still in progress
        7: 0.65,  # Week 7: VERY HIGH - tests passing, ready for deploy
        8: 0.30,  # Week 8: Moderate - deployment in progress
    }
    
    # Lane flow rates: how many cards reach each stage
    # backlog -> in_progress -> review -> done
    LANE_FLOW_RATES = {
        1: {"in_progress": 0.30, "review": 0.10, "done": 0.05},   # Week 1: mostly backlog
        2: {"in_progress": 0.40, "review": 0.20, "done": 0.15},   # Week 2: more flow
        3: {"in_progress": 0.35, "review": 0.25, "done": 0.25},   # Week 3: high throughput
        4: {"in_progress": 0.45, "review": 0.25, "done": 0.20},   # Week 4: good flow
        5: {"in_progress": 0.40, "review": 0.30, "done": 0.30},   # Week 5: strong flow
        6: {"in_progress": 0.50, "review": 0.30, "done": 0.15},   # Week 6: high in-progress
        7: {"in_progress": 0.20, "review": 0.30, "done": 0.65},   # Week 7: HIGH throughput to done
        8: {"in_progress": 0.40, "review": 0.35, "done": 0.25},   # Week 8: balanced flow
    }
    
    total_cards_planned = sum(WEEK_CARD_COUNTS.values())
    
    print(f"\n📊 Generating {WEEKS} weeks of historical cards ({total_cards_planned} total)...\n")
    print(f"   Week 1-8 card counts: {', '.join(str(WEEK_CARD_COUNTS[i]) for i in range(1, 9))}")
    print(f"   Archive rates: {', '.join(f'{ARCHIVE_RATES[i]:.0%}' for i in range(1, 9))}")
    print(f"   Timeline: {start_date.strftime('%b %d')} → {today.strftime('%b %d, %Y')}\n")
    
    random.seed(42)  # For reproducibility
    
    # Feature categories for the AI News Platform
    FEATURE_CATEGORIES = {
        "Week 1 - Analytics Foundation": {
            "features": [
                "Event tracking system",
                "User engagement metrics",
                "Page view analytics",
                "Session management",
                "Data aggregation pipeline",
            ],
            "bugs": [
                "Fix event timestamp inconsistency",
                "Fix duplicate event tracking",
                "Fix timezone handling in UTC conversion",
            ],
            "enhancements": [
                "Optimize event storage",
                "Improve query performance",
                "Add caching layer",
            ],
        },
        "Week 2 - Data Collection": {
            "features": [
                "Implement article feed parser",
                "Add RSS feed support",
                "Create crawling scheduler",
                "Build source management API",
                "Implement content deduplication",
            ],
            "bugs": [
                "Fix HTML parsing edge cases",
                "Fix XML feed validation",
                "Fix encoding detection",
            ],
            "enhancements": [
                "Optimize memory usage in crawlers",
                "Add parallel processing",
                "Improve error recovery",
            ],
        },
        "Week 3 - Content Processing": {
            "features": [
                "Implement text extraction",
                "Add NLP tokenization",
                "Build keyword extraction",
                "Create summary generation",
                "Implement entity recognition",
            ],
            "bugs": [
                "Fix summary length calculation",
                "Fix keyword ranking algorithm",
                "Fix entity extraction for rare types",
            ],
            "enhancements": [
                "Improve NLP model accuracy",
                "Optimize tokenization speed",
                "Add multi-language support",
            ],
        },
        "Week 4 - AI Recommendations": {
            "features": [
                "Build recommendation engine",
                "Implement collaborative filtering",
                "Add content-based filtering",
                "Create ranking algorithm",
                "Build personalization logic",
            ],
            "bugs": [
                "Fix recommendation diversity",
                "Fix cold start problem",
                "Fix ranking consistency",
            ],
            "enhancements": [
                "Optimize ML model training",
                "Add A/B testing framework",
                "Improve recommendation speed",
            ],
        },
        "Week 5 - User Interface": {
            "features": [
                "Build feed component",
                "Create article detail page",
                "Implement search interface",
                "Add filter controls",
                "Build preference panel",
            ],
            "bugs": [
                "Fix responsive design issues",
                "Fix component rendering performance",
                "Fix accessibility compliance",
            ],
            "enhancements": [
                "Improve UI responsiveness",
                "Add dark mode support",
                "Optimize bundle size",
            ],
        },
        "Week 6 - API Development": {
            "features": [
                "Build REST API endpoints",
                "Implement GraphQL schema",
                "Create authentication system",
                "Add rate limiting",
                "Build WebSocket for real-time updates",
            ],
            "bugs": [
                "Fix CORS issues",
                "Fix API error handling",
                "Fix timeout handling",
            ],
            "enhancements": [
                "Improve API performance",
                "Add request caching",
                "Optimize database queries",
            ],
        },
        "Week 7 - Testing & QA": {
            "features": [
                "Create unit test suite",
                "Build integration tests",
                "Implement e2e test automation",
                "Add performance benchmarks",
                "Create analytics verification suite",
            ],
            "bugs": [
                "Fix flaky tests",
                "Fix test isolation issues",
                "Fix mock data generation",
            ],
            "enhancements": [
                "Improve test coverage",
                "Optimize test execution time",
                "Add mutation testing",
            ],
        },
        "Week 8 - Deployment & Monitoring": {
            "features": [
                "Setup CI/CD pipeline",
                "Implement logging system",
                "Create monitoring dashboards",
                "Build alerting rules",
                "Create deployment automation",
            ],
            "bugs": [
                "Fix deployment failures",
                "Fix log rotation issues",
                "Fix metric collection gaps",
            ],
            "enhancements": [
                "Improve deployment speed",
                "Add canary deployment",
                "Optimize logging overhead",
            ],
        },
    }
    
    PRIORITIES = ["low", "medium", "high"]
    LANES = ["backlog", "in_progress", "review", "done"]
    
    card_number = 1
    cards_by_week = {}
    archived_count = 0
    
    # Generate cards week by week - ALL IN BACKLOG
    print("🎯 Phase 1: Creating all cards from past 8 weeks...\n")
    
    for week_idx in range(1, WEEKS + 1):
        week_start = start_date + timedelta(days=(week_idx - 1) * 7)
        week_end = week_start + timedelta(days=6)
        
        category_key = f"Week {week_idx} - "
        category_name = [k for k in FEATURE_CATEGORIES.keys() if k.startswith(category_key)][0]
        category = FEATURE_CATEGORIES[category_name]
        
        cards_this_week_count = WEEK_CARD_COUNTS[week_idx]
        
        print(f"Week {week_idx} ({week_start.strftime('%Y-%m-%d')} to {week_end.strftime('%Y-%m-%d')}) - {cards_this_week_count} cards")
        
        cards_this_week = []
        
        # Distribute cards across types (60% features, 25% bugs, 15% enhancements)
        feature_count = (cards_this_week_count * 60) // 100
        bug_count = (cards_this_week_count * 25) // 100
        enhancement_count = cards_this_week_count - feature_count - bug_count
        
        # Create feature cards - ALL IN BACKLOG
        for i in range(feature_count):
            feature_desc = category["features"][i % len(category["features"])]
            task_num = i + 1
            title = f"[Week {week_idx}] Feature: {feature_desc} - Part {task_num}"
            
            # Stagger dates across the week
            card_date = week_start + timedelta(days=(i % 7))
            
            # Some cards have deadlines (about 70%)
            due_date = None
            if i % 10 < 7:
                due_date = date_to_ymd(card_date + timedelta(days=3 + (i % 4)))
            
            # Priority: features tend toward medium/high
            priority = PRIORITIES[(i % 3) + 1] if i % 3 < 3 else "medium"
            
            tags = [
                "feature",
                f"week-{week_idx}",
                "ai-platform",
                f"date:{card_date.strftime('%Y-%m-%d')}",
            ]
            
            try:
                card = operations.create_card(
                    title=title,
                    lane="backlog",  # ALL START IN BACKLOG
                    description=f"Part of Week {week_idx} development sprint. {feature_desc}. This is an AI news platform development task.",
                    tags=tags,
                    priority=priority,
                    epic=epic_name,
                    due_date=due_date,
                )
                
                # Backdate the card to when it was created in the sprint
                card.created_at = date_to_iso(card_date - timedelta(hours=random.randint(0, 48)))
                card.updated_at = card.created_at
                markdown_store.write_card(card)
                sqlite_cache.upsert_card(card)
                
                cards_this_week.append(card)
                card_number += 1
            except Exception as e:
                pass
        
        # Create bug cards - ALL IN BACKLOG
        for i in range(bug_count):
            bug_desc = category["bugs"][i % len(category["bugs"])]
            title = f"[Week {week_idx}] Bug: {bug_desc}"
            
            card_date = week_start + timedelta(days=2 + (i % 5))
            
            # Bugs more likely to have deadlines (80%)
            due_date = None
            if i % 10 < 8:
                due_date = date_to_ymd(card_date + timedelta(days=1 + (i % 2)))
            
            # Bugs are high priority
            priority = PRIORITIES[2]
            
            tags = [
                "bug",
                f"week-{week_idx}",
                "normal-fix",
                f"date:{card_date.strftime('%Y-%m-%d')}",
            ]
            
            try:
                card = operations.create_card(
                    title=title,
                    lane="backlog",  # ALL START IN BACKLOG
                    description=f"Bug fix for Week {week_idx}. {bug_desc}. AI news platform stability issue.",
                    tags=tags,
                    priority=priority,
                    epic=epic_name,
                    due_date=due_date,
                )
                
                # Backdate the card to when it was created in the sprint
                card.created_at = date_to_iso(card_date - timedelta(hours=random.randint(0, 48)))
                card.updated_at = card.created_at
                markdown_store.write_card(card)
                sqlite_cache.upsert_card(card)
                
                cards_this_week.append(card)
                card_number += 1
            except Exception as e:
                pass
        
        # Create enhancement cards - ALL IN BACKLOG
        for i in range(enhancement_count):
            enhancement_desc = category["enhancements"][i % len(category["enhancements"])]
            title = f"[Week {week_idx}] Enhancement: {enhancement_desc}"
            
            card_date = week_start + timedelta(days=3 + (i % 4))
            
            # Enhancements less likely to have deadlines (40%)
            due_date = None
            if i % 10 < 4:
                due_date = date_to_ymd(card_date + timedelta(days=4 + (i % 3)))
            
            # Enhancements are low/medium priority
            priority = PRIORITIES[(i % 2)]
            
            tags = [
                "enhancement",
                f"week-{week_idx}",
                "tech-debt" if i % 3 == 0 else "optimization",
                f"date:{card_date.strftime('%Y-%m-%d')}",
            ]
            
            try:
                card = operations.create_card(
                    title=title,
                    lane="backlog",  # ALL START IN BACKLOG
                    description=f"Enhancement for Week {week_idx}. {enhancement_desc}. Improve platform quality.",
                    tags=tags,
                    priority=priority,
                    epic=epic_name,
                    due_date=due_date,
                )
                
                # Backdate the card to when it was created in the sprint
                card.created_at = date_to_iso(card_date - timedelta(hours=random.randint(0, 48)))
                card.updated_at = card.created_at
                markdown_store.write_card(card)
                sqlite_cache.upsert_card(card)
                
                cards_this_week.append(card)
                card_number += 1
            except Exception as e:
                pass
        
        cards_by_week[week_idx] = cards_this_week
        print(f"  ✓ Created {len(cards_this_week)} cards in backlog")

    # Fix null→backlog transition timestamps.
    # operations.create_card records the transition at _now() before the card is
    # backdated, so every creation event lands on today. Overwrite transitions.jsonl
    # with the correct backdated times, then replay into the SQLite cache.
    print("\n🕐 Fixing creation transition timestamps...")
    all_created = [card for cards in cards_by_week.values() for card in cards]
    all_created.sort(key=lambda c: c.created_at)
    from paths import TRANSITIONS_LOG
    TRANSITIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(TRANSITIONS_LOG, "w", encoding="utf-8") as fh:
        for card in all_created:
            fh.write(json.dumps({"card_id": card.id, "from": None, "to": "backlog", "at": card.created_at}) + "\n")
    event_log.replay_into_cache()
    print(f"  ✓ Rewrote {len(all_created)} creation events with correct timestamps")

    print(f"\n🌊 Phase 2: Simulating card flow through lanes...\n")
    
    # Now move cards through lanes to simulate workflow
    lane_transitions = {"backlog": 0, "in_progress": 0, "review": 0, "done": 0}
    
    for week_idx in range(1, WEEKS + 1):
        week_start = start_date + timedelta(days=(week_idx - 1) * 7)  # recalculate per week
        cards_this_week = cards_by_week[week_idx]
        flows = LANE_FLOW_RATES[week_idx]
        archive_rate = ARCHIVE_RATES[week_idx]
        
        # Determine how many cards reach each stage
        in_progress_count = max(1, int(len(cards_this_week) * flows["in_progress"]))
        review_count = max(0, int(len(cards_this_week) * flows["review"]))
        done_count = max(0, int(len(cards_this_week) * flows["done"]))
        
        # Shuffle cards to randomize which ones flow
        shuffled = cards_this_week.copy()
        random.shuffle(shuffled)
        
        # Proper chain: backlog → in_progress → review → done
        # Pool for in_progress is the full shuffled list
        to_in_progress = shuffled[:in_progress_count]
        # Pool for review is a subset of those that went to in_progress
        to_review = to_in_progress[:review_count]
        # Pool for done is a subset of those that went to review
        to_done = to_review[:done_count]

        # Move backlog → in_progress (early in the week)
        for card in to_in_progress:
            transition_time = week_start + timedelta(days=random.randint(0, 3), hours=random.randint(8, 17))
            if move_card_backdated(card.id, "in_progress", transition_time):
                lane_transitions["in_progress"] += 1
        
        # Move in_progress → review (mid-week)
        for card in to_review:
            transition_time = week_start + timedelta(days=random.randint(2, 4), hours=random.randint(8, 17))
            if move_card_backdated(card.id, "review", transition_time):
                lane_transitions["review"] += 1
        
        # Move review → done (late in the week)
        for card in to_done:
            transition_time = week_start + timedelta(days=random.randint(4, 6), hours=random.randint(8, 17))
            if move_card_backdated(card.id, "done", transition_time):
                lane_transitions["done"] += 1
        
        # Archive some cards based on week archive rate (backdate archives)
        for card in shuffled:
            if random.random() < archive_rate:
                try:
                    # Backdate archive to end of week
                    operations.archive_card(card.id)
                    card = markdown_store.read_card(card.id)
                    card.updated_at = date_to_iso(week_start + timedelta(days=random.randint(5, 6), hours=random.randint(8, 17)))
                    markdown_store.write_card(card)
                    sqlite_cache.upsert_card(card)
                    archived_count += 1
                except Exception as e:
                    pass
        
        print(f"Week {week_idx}: →in_progress:{in_progress_count} →review:{review_count} →done:{done_count} (archive:{archive_rate:.0%})")
    
    print(f"\n🔗 Phase 3: Adding relationships between cards...\n")
    
    # Create relationships within weeks and across weeks
    relationships_created = 0
    
    # Within-week relationships (dependencies within same week)
    for week_idx, cards in cards_by_week.items():
        if len(cards) < 2:
            continue
            
        # Link some cards as dependencies
        for i in range(min(5, len(cards) // 2)):
            try:
                from_idx = i * 2
                to_idx = i * 2 + 1
                if to_idx < len(cards):
                    from_card = cards[from_idx]
                    to_card = cards[to_idx]
                    if from_card.number and to_card.number:
                        # Features block other features, bugs are critical
                        link_type = "blocks" if "feature" in from_card.tags else "relates_to"
                        error = operations.link_cards(from_card.number, to_card.number, link_type)
                        if not error:
                            relationships_created += 1
            except Exception as e:
                pass
    
    # Cross-week relationships (dependencies between weeks)
    week_nums = sorted(cards_by_week.keys())
    for i in range(len(week_nums) - 1):
        current_week = week_nums[i]
        next_week = week_nums[i + 1]
        
        current_cards = cards_by_week[current_week]
        next_cards = cards_by_week[next_week]
        
        if len(current_cards) == 0 or len(next_cards) == 0:
            continue
        
        # Link some cards from current week to next week
        try:
            # Get feature cards from current week
            feature_cards = [c for c in current_cards if "feature" in c.tags]
            if feature_cards:
                # Link to first few cards of next week
                from_card = feature_cards[0]
                to_cards = next_cards[:min(2, len(next_cards))]
                
                for to_card in to_cards:
                    if from_card.number and to_card.number:
                        error = operations.link_cards(from_card.number, to_card.number, "parent_of")
                        if not error:
                            relationships_created += 1
        except Exception as e:
            pass
    
    print(f"  ✓ Created {relationships_created} relationships\n")
    
    total_created = card_number - 1
    
    print(f"✅ Epic generation complete!")
    print(f"   Epic: {epic_name}")
    print(f"   Total cards created: {total_created}")
    print(f"   Weeks: {WEEKS}")
    print(f"   Relationships: {relationships_created}")
    print(f"\n📊 Summary:")
    print(f"   Features (60%): ~{(total_created * 60) // 100}")
    print(f"   Bugs (25%): ~{(total_created * 25) // 100}")
    print(f"   Enhancements (15%): ~{(total_created * 15) // 100}")
    
    print(f"\n🌊 Lane Transitions (Pull & Throughput):")
    print(f"   Backlog (start): {total_created}")
    print(f"   → In Progress: {lane_transitions['in_progress']}")
    print(f"   → Review: {lane_transitions['review']}")
    print(f"   → Done: {lane_transitions['done']}")
    print(f"   Archived: {archived_count}")
    
    print(f"\n📈 Throughput by Week:")
    for week_idx in range(1, WEEKS + 1):
        flows = LANE_FLOW_RATES[week_idx]
        count = WEEK_CARD_COUNTS[week_idx]
        in_prog = int(count * flows["in_progress"])
        review = int(count * flows["review"])
        done = int(count * flows["done"])
        print(f"   Week {week_idx}: {done}/{count} to Done ({(done/count)*100:.0f}% throughput)")


if __name__ == "__main__":
    create_debug_analytics_epic()
