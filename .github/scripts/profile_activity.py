import argparse
from datetime import date, datetime, time, timedelta
from html import escape
import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo


PROFILE_TIME = ZoneInfo("Asia/Yekaterinburg")

STATS_QUERY = """
query($login: String!, $prs: String!, $issues: String!, $after: String) {
  user(login: $login) {
    name
    createdAt
    followers { totalCount }
    repositories(first: 100, after: $after, privacy: PUBLIC, ownerAffiliations: OWNER) {
      nodes { stargazerCount }
      pageInfo { endCursor hasNextPage }
    }
    repositoriesContributedTo(first: 1, privacy: PUBLIC,
      contributionTypes: [COMMIT, ISSUE, PULL_REQUEST, REPOSITORY]) { totalCount }
    contributionsCollection {
      totalCommitContributions
      totalPullRequestReviewContributions
    }
  }
  prs: search(query: $prs, type: ISSUE, first: 1) { issueCount }
  issues: search(query: $issues, type: ISSUE, first: 1) { issueCount }
}
"""

CALENDAR_QUERY = """
query($login: String!, $start: DateTime!, $end: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $start, to: $end) {
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def graphql(query, **variables):
    result = subprocess.run(
        ["gh", "api", "graphql", "--input", "-"],
        input=json.dumps({"query": query, "variables": variables}),
        text=True,
        capture_output=True,
        check=True,
    )
    response = json.loads(result.stdout)
    if response.get("errors"):
        raise RuntimeError("; ".join(error["message"] for error in response["errors"]))
    return response["data"]


def get_stats(login):
    variables = {
        "login": login,
        "prs": f"is:pr is:public author:{login}",
        "issues": f"is:issue is:public author:{login}",
    }
    stats = graphql(STATS_QUERY, **variables)
    repositories = stats["user"]["repositories"]
    stars = sum(repo["stargazerCount"] for repo in repositories["nodes"])
    while repositories["pageInfo"]["hasNextPage"]:
        repositories = graphql(
            STATS_QUERY, **variables, after=repositories["pageInfo"]["endCursor"]
        )["user"]["repositories"]
        stars += sum(repo["stargazerCount"] for repo in repositories["nodes"])
    return stats, stars


def get_history(login, joined, now):
    days = {}
    for year in range(joined.year, now.year + 1):
        first = max(joined, date(year, 1, 1))
        last = min(now.date(), date(year, 12, 31))
        start = datetime.combine(first, time.min, PROFILE_TIME)
        end = min(now, datetime.combine(last, time(23, 59, 59), PROFILE_TIME))
        calendar = graphql(
            CALENDAR_QUERY, login=login, start=start.isoformat(), end=end.isoformat()
        )["user"]["contributionsCollection"]["contributionCalendar"]
        for week in calendar["weeks"]:
            for day in week["contributionDays"]:
                day_date = date.fromisoformat(day["date"])
                if first <= day_date <= last:
                    days[day_date] = day["contributionCount"]
    return days


def streaks(days, today):
    previous = None
    length = 0
    current = longest = (0, None, None)
    one_day = timedelta(days=1)
    for day in sorted(day for day, count in days.items() if count > 0):
        length = length + 1 if previous and day - previous == one_day else 1
        streak = (length, day - (length - 1) * one_day, day)
        if length > longest[0]:
            longest = streak
        if (today - day).days <= 1:
            current = streak
        previous = day
    return current, longest


def rank(commits, prs, issues, reviews, stars, followers):
    # GitHub Readme Stats rank formula (MIT, Anurag Hazra).
    # https://github.com/anuraghazra/github-readme-stats/blob/master/src/calculateRank.js
    weighted_activity = (
        2 * (1 - 2 ** (-commits / 250))
        + 3 * (1 - 2 ** (-prs / 50))
        + (1 - 2 ** (-issues / 25))
        + (1 - 2 ** (-reviews / 2))
        + 4 * stars / (50 + stars)
        + followers / (10 + followers)
    )
    percentile = 100 * (1 - weighted_activity / 12)
    levels = [(1, "S"), (12.5, "A+"), (25, "A"), (37.5, "A-"), (50, "B+"),
              (62.5, "B"), (75, "B-"), (87.5, "C+"), (100, "C")]
    return next(label for cutoff, label in levels if percentile <= cutoff), 100 - percentile


def svg(title, description, height, content):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="560" height="{height}" viewBox="0 0 560 {height}" role="img" aria-labelledby="title desc">
  <title id="title">{escape(title)}</title>
  <desc id="desc">{escape(description)}</desc>
  <style>
    text {{ font-family: "Segoe UI", Arial, sans-serif; fill: #c9d1d9; }}
    .title {{ fill: #f8515a; font-size: 20px; font-weight: 700; }}
    .label {{ font-size: 14px; font-weight: 600; }}
    .value {{ fill: #f0f6fc; font-size: 16px; font-weight: 700; }}
    .number {{ fill: #58a6ff; font-size: 32px; font-weight: 700; }}
    .detail {{ fill: #8b949e; font-size: 11px; }}
    @keyframes reveal {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
    .content {{ animation: reveal 0.45s ease-out both; }}
    @media (prefers-reduced-motion: reduce) {{ .content {{ animation: none; }} }}
  </style>
  <rect x=".5" y=".5" width="559" height="{height - 1}" rx="12" fill="#0d1117" stroke="#30363d"/>
  <g class="content">
{content}
  </g>
</svg>
"""


def stats_card(login, stats, stars, updated):
    user = stats["user"]
    name = user["name"] or login
    title = f"{name}'s GitHub Stats"
    contributions = user["contributionsCollection"]
    commits = contributions["totalCommitContributions"]
    prs = stats["prs"]["issueCount"]
    issues = stats["issues"]["issueCount"]
    grade, score = rank(
        commits, prs, issues, contributions["totalPullRequestReviewContributions"],
        stars, user["followers"]["totalCount"],
    )
    rows = (
        ("Stars earned", stars, "m8 1 2 4 4.5.6-3.3 3.2.8 4.5L8 11.2l-4 2.1.8-4.5L1.5 5.6 6 5Z"),
        ("Commits (last year)", commits, "M8 4v4l2.5 2M14 8A6 6 0 1 1 8 2a6 6 0 0 1 6 6Z"),
        ("Pull requests", prs, "M4 5.5v5m8 0V8a3 3 0 0 0-3-3H8m2-2L8 5l2 2M4 2a1.75 1.75 0 1 0 0 3.5A1.75 1.75 0 0 0 4 2Zm0 8.5a1.75 1.75 0 1 0 0 3.5 1.75 1.75 0 0 0 0-3.5Zm8 0a1.75 1.75 0 1 0 0 3.5 1.75 1.75 0 0 0 0-3.5Z"),
        ("Issues", issues, "M8 4v4m0 2.5v.5M14 8A6 6 0 1 1 8 2a6 6 0 0 1 6 6Z"),
        ("Other repositories contributed to", user["repositoriesContributedTo"]["totalCount"],
         "M4 12V3h8v9H4a1 1 0 0 0 0 2h8M6 5h4"),
    )
    content = [f'    <text x="24" y="34" class="title">{escape(title)}</text>']
    for index, (label, value, icon) in enumerate(rows):
        y = 65 + index * 25
        content.extend((
            f'    <path d="{icon}" transform="translate(24,{y - 12})" fill="none" stroke="#f8515a" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>',
            f'    <text x="49" y="{y}" class="label">{label}</text>',
            f'    <text x="335" y="{y}" class="value" text-anchor="end">{value:,}</text>',
        ))
    content.extend((
        '    <circle cx="450" cy="108" r="42" fill="none" stroke="#492327" stroke-width="6"/>',
        f'    <circle cx="450" cy="108" r="42" fill="none" stroke="#f8515a" stroke-width="6" pathLength="100" stroke-dasharray="{score:.2f} 100" stroke-linecap="round" transform="rotate(-90 450 108)"/>',
        f'    <text x="450" y="119" class="number" style="fill:#f0f6fc" text-anchor="middle">{grade}</text>',
        f'    <text x="24" y="198" class="detail">{updated}</text>',
    ))
    description = ", ".join(f"{label}: {value}" for label, value, _ in rows)
    return svg(title, f"{description}, rank {grade}. {updated}.", 216, "\n".join(content))


def format_streak(streak):
    length, first, last = streak
    if length == 0:
        return "—"
    if first == last:
        return first.strftime("%b %d, %Y")
    if first.year == last.year:
        return f"{first:%b %d} – {last:%b %d, %Y}"
    return f"{first:%b %d, %Y} – {last:%b %d, %Y}"


def streak_card(days, joined, today, updated):
    current, longest = streaks(days, today)
    total = sum(days.values())
    return svg(
        "GitHub Contribution Streak",
        f"Total contributions: {total}. Current streak: {current[0]} days. Longest streak: {longest[0]} days. {updated}.",
        192,
        f"""    <path d="M187 20v132m186-132v132" stroke="#30363d"/>
    <circle cx="280" cy="66" r="39" fill="none" stroke="#2f81f7" stroke-width="4"/>
    <path d="M280 14c2 5 6 6 6 11a6 6 0 0 1-12 0c0-3 2-5 4-7 0 3 1 4 2 4 2-2 1-5 0-8Z" fill="#58a6ff" stroke="#0d1117" stroke-width="2"/>
    <g text-anchor="middle">
      <text x="94" y="76" class="number">{total:,}</text>
      <text x="280" y="77" class="number" style="fill:#f0f6fc">{current[0]:,}</text>
      <text x="466" y="76" class="number">{longest[0]:,}</text>
      <text x="94" y="125" class="label">Total Contributions</text>
      <text x="280" y="125" class="label">Current Streak</text>
      <text x="466" y="125" class="label">Longest Streak</text>
      <text x="94" y="146" class="detail">{joined:%b %d, %Y} – Present</text>
      <text x="280" y="146" class="detail">{format_streak(current)}</text>
      <text x="466" y="146" class="detail">{format_streak(longest)}</text>
    </g>
    <text x="24" y="175" class="detail">{updated}</text>""",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("login")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    now = datetime.now(PROFILE_TIME)
    stats, stars = get_stats(args.login)
    joined = datetime.fromisoformat(stats["user"]["createdAt"]).astimezone(PROFILE_TIME).date()
    days = get_history(args.login, joined, now)
    updated = f"Updated {now:%Y-%m-%d %H:%M} (UTC+05:00)"

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "github-stats.svg").write_text(
        stats_card(args.login, stats, stars, updated), encoding="utf-8"
    )
    (args.output / "github-streak.svg").write_text(
        streak_card(days, joined, now.date(), updated), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
