import os
import sys
import re
import json
import hashlib
import urllib.request
import difflib

# Configuration
# deepwiki.com itself sits behind a Vercel JS challenge ("Security Checkpoint",
# HTTP 429) as of 2026-10-15, so scraping the HTML no longer works. DeepWiki's
# official MCP server serves the same wiki content without the challenge.
MCP_URL = "https://mcp.deepwiki.com/mcp"
REPO_NAME = "haiku/haiku"
WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(WORKSPACE_DIR, "haiku-deepwiki-md")
METADATA_FILE = os.path.join(OUTPUT_DIR, "metadata.json")

# Ensure folders exist
os.makedirs(OUTPUT_DIR, exist_ok=True)

def call_mcp_tool(name, arguments):
    """Call a tool on the DeepWiki MCP server (streamable HTTP) and return its text result."""
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    req = urllib.request.Request(MCP_URL, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=300) as response:
        raw = response.read().decode("utf-8")
        content_type = response.info().get("Content-Type", "")

    # The server answers either with plain JSON or with an SSE stream of
    # "data: {...}" lines; take the JSON-RPC message that carries our result.
    if "text/event-stream" in content_type:
        messages = []
        for line in raw.splitlines():
            if line.startswith("data:"):
                messages.append(json.loads(line[len("data:"):].strip()))
    else:
        messages = [json.loads(raw)]

    for msg in messages:
        if msg.get("id") != 1:
            continue
        if "error" in msg:
            raise RuntimeError(f"MCP error from {name}: {msg['error']}")
        result = msg["result"]
        text = "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        if result.get("isError"):
            raise RuntimeError(f"MCP tool {name} failed: {text}")
        return text
    raise RuntimeError(f"No result for MCP tool {name}")

def slugify(number, title):
    """Build the DeepWiki URL slug, e.g. ('1.1', 'Key Features and Subsystems') -> '1.1-key-features-and-subsystems'."""
    words = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    return f"{number}-{words}"

def fetch_structure():
    """Return the ordered table of contents as a list of (slug, title)."""
    text = call_mcp_tool("read_wiki_structure", {"repoName": REPO_NAME})
    links = []
    for line in text.splitlines():
        m = re.match(r'^\s*-\s+([\d.]+)\s+(.+?)\s*$', line)
        if m:
            links.append((slugify(m.group(1), m.group(2)), m.group(2)))
    return links

def fetch_pages():
    """Return {title: markdown} for every page in the wiki."""
    text = call_mcp_tool("read_wiki_contents", {"repoName": REPO_NAME})
    pages = {}
    # Pages are concatenated as "# Page: <title>\n\n<markdown>\n\n---\n\n"
    chunks = re.split(r'^# Page: (.*)\n', text, flags=re.M)
    for i in range(1, len(chunks) - 1, 2):
        title = chunks[i].strip()
        body = chunks[i + 1].strip("\n")
        if body.endswith("\n---"):
            body = body[:-len("\n---")].rstrip("\n")
        pages[title] = body
    return text, pages

def normalize_title(title):
    """Normalize a title for matching, removing formatting and special characters."""
    if title.startswith("#"):
        title = title.lstrip("#").strip()
    title = title.lower()
    title = re.sub(r'[^a-z0-9]', '', title)
    return title

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Convert Haiku DeepWiki documentation to Markdown.")
    parser.add_argument("--force", action="store_true", help="Force rebuild of documentation even if the remote content hash is unchanged.")
    parser.add_argument("--single", type=str, help="Process only a single slug (e.g. 1-overview-of-haiku-os) and print it.")
    parser.add_argument("--dry-run", action="store_true", help="Run the script but do not write any files.")
    args = parser.parse_args()

    print("Fetching Haiku DeepWiki table of contents via MCP...")
    unique_links = fetch_structure()
    print(f"Discovered {len(unique_links)} documentation pages.")

    print("Fetching Haiku DeepWiki contents via MCP (this can take a minute)...")
    full_text, pages = fetch_pages()
    print(f"Received {len(pages)} pages.")

    # Map pages to slugs by title
    norm_pages = {normalize_title(t): body for t, body in pages.items()}
    slug_to_markdown = {}
    for slug, title in unique_links:
        content = norm_pages.get(normalize_title(title))
        if content:
            slug_to_markdown[slug] = content

    # Single slug mode
    if args.single:
        print(f"Single slug mode: {args.single}")
        matched_payload = slug_to_markdown.get(args.single)
        if matched_payload:
            print("--- CONVERTED MARKDOWN PREVIEW ---")
            print(matched_payload[:4000])
            if len(matched_payload) > 4000:
                print("... (truncated) ...")
        else:
            print(f"Could not find page for slug '{args.single}'. Available slugs:")
            for slug, _ in unique_links:
                print("  ", slug)
        return

    # Change detection: hash of the full wiki text (the MCP server sends no ETag)
    content_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()

    metadata = {}
    if os.path.exists(METADATA_FILE):
        try:
            with open(METADATA_FILE, "r", encoding="utf-8") as f:
                metadata = json.load(f)
        except Exception:
            pass

    stored_hash = metadata.get("content_sha256")
    print(f"Remote content hash: {content_hash}")
    print(f"Stored content hash: {stored_hash}")

    if not args.force and stored_hash == content_hash:
        print("Haiku DeepWiki documentation is up to date. No changes to apply.")
        return

    missing = [slug for slug, _ in unique_links if slug not in slug_to_markdown]
    if missing:
        print(f"Warning: {len(missing)} pages had no content: {', '.join(missing)}", file=sys.stderr)

    # Write files differentially
    print("Writing Markdown files...")
    written = 0
    updated = 0
    unchanged = 0
    active_paths = set()

    # Determine index page content (overview page)
    overview_slug = unique_links[0][0] if unique_links else ""
    overview_content = slug_to_markdown.get(overview_slug, "")

    for slug, title in unique_links:
        content = slug_to_markdown.get(slug)
        if not content:
            print(f"Skipping {slug} (no content found)")
            continue

        filename = f"{slug}.md"
        full_path = os.path.join(OUTPUT_DIR, filename)
        active_paths.add(filename)

        if os.path.exists(full_path):
            with open(full_path, "r", encoding="utf-8") as f:
                old_content = f.read()
            if old_content == content:
                unchanged += 1
                continue

            updated += 1
            diff = difflib.unified_diff(
                old_content.splitlines(),
                content.splitlines(),
                fromfile=f"a/{filename}",
                tofile=f"b/{filename}",
                lineterm=""
            )
            diff_text = "\n".join(list(diff)[:15])

            if not args.dry_run:
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
                print(f"UPDATED: {filename}")
                if diff_text:
                    print("DIFF PREVIEW:\n" + diff_text + "\n" + "-"*40)
            else:
                print(f"DRY-RUN UPDATED: {filename}")
                if diff_text:
                    print("DIFF PREVIEW:\n" + diff_text + "\n" + "-"*40)
        else:
            written += 1
            if not args.dry_run:
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
                print(f"CREATED: {filename}")
            else:
                print(f"DRY-RUN CREATED: {filename}")

    # Generate README.md containing the overview content + Table of Contents
    readme_path = os.path.join(OUTPUT_DIR, "README.md")
    active_paths.add("README.md")

    toc_lines = ["\n## Documentation Index\n", "Here is the complete list of chapters and articles available in this reference:\n"]
    for slug, title in unique_links:
        toc_lines.append(f"- [{title}]({slug}.md)")
    toc_text = "\n".join(toc_lines) + "\n"

    # README content is overview content + TOC index
    readme_content = overview_content.strip() + "\n\n" + toc_text

    if os.path.exists(readme_path):
        with open(readme_path, "r", encoding="utf-8") as f:
            old_readme = f.read()
        if old_readme != readme_content:
            if not args.dry_run:
                with open(readme_path, "w", encoding="utf-8") as f:
                    f.write(readme_content)
                print("UPDATED: README.md")
            else:
                print("DRY-RUN UPDATED: README.md")
        else:
            unchanged += 1
    else:
        written += 1
        if not args.dry_run:
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(readme_content)
            print("CREATED: README.md")
        else:
            print("DRY-RUN CREATED: README.md")

    # Clean up obsolete markdown files (only if the fetch looked complete, so a
    # partial MCP response can't wipe the local copy)
    if not missing:
        for file in os.listdir(OUTPUT_DIR):
            if file.endswith(".md"):
                if file not in active_paths:
                    print(f"DELETED: {file} (no longer in remote documentation)")
                    if not args.dry_run:
                        os.remove(os.path.join(OUTPUT_DIR, file))

    print(f"\nSummary:")
    print(f"  Created: {written}")
    print(f"  Updated: {updated}")
    print(f"  Unchanged: {unchanged}")

    # Save metadata
    if not args.dry_run:
        metadata = {
            "content_sha256": content_hash,
            "files": list(active_paths)
        }
        with open(METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
        print("Updated metadata.")

if __name__ == '__main__':
    main()
