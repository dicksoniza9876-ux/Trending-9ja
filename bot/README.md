# Trending9ja auto-update bot

Runs on GitHub (free). Every 3 hours it reads the feeds in `feeds.json`, asks Gemini to write short original briefs,
builds the story pages, makes a green headline image for each, and updates the homepage, category pages, archive and sitemap.

## Files
- `update.py` - the bot
- `feeds.json` - the sites it reads (edit freely; broken links are skipped)
- `config.json` - limits (30 stories/day max, 5 per run, which categories need approval)
- `drafts/` - sensitive stories waiting for your approval
- `gist.txt` - today's "Gist of the Day" WhatsApp message
- `state.json` - the bot's memory (what it has already seen)

## Approving drafts (phone friendly)
Actions > Auto update site > Run workflow > type the draft ids in `approve_ids` (or `reject_ids`) > Run.
The ids are listed in the summary of each run.
