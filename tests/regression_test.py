#!/usr/bin/env python3
"""
mc-search Regression Test Suite v2 — Batch 2 modifications verification.

Tests all core search functions, parsers, cache, and error handling.
Exits with non-zero status on any failure.
"""

import sys
import os
import json
import time

# ── Setup ─────────────────────────────────────────────
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'skills/mc-search'))

passed = 0
failed = 0
skipped = 0

def check(description: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {description}")
    else:
        failed += 1
        msg = f"  FAIL: {description}"
        if detail:
            msg += f" — {detail}"
        print(msg)

def skip(description: str):
    global skipped
    skipped += 1
    print(f"  SKIP: {description}")

# ── 1. Import Validation ─────────────────────────────
print("\n" + "=" * 60)
print("SECTION 1: Import Validation")
print("=" * 60)

from scripts import core

# Check all expected public functions exist
expected_funcs = [
    'search_mcmod', 'search_all', 'search_modrinth', 'search_wiki', 'search_wiki_zh',
    'fetch_mod_info', 'get_mod_dependencies', 'search_mcmod_author', 'search_modrinth_author',
    'search_mcmod_modpack', 'read_wiki', 'read_wiki_zh', 'set_cache', 'set_platform_enabled',
    'curl', '_parse_mcmod_search_results', '_extract_search_result_metadata',
]
for fname in expected_funcs:
    check(f"Function exists: {fname}", hasattr(core, fname),
          f"Missing function: {fname}")

# Check constants
check("Constant MIN_HTML_LEN exists", hasattr(core, 'MIN_HTML_LEN'))
check("Constant HTTP_HEADERS exists", hasattr(core, 'HTTP_HEADERS'))

# Check SearchError
check("SearchError is Exception subclass", issubclass(core.SearchError, Exception))

# ── 2. set_platform_enabled / set_cache smoke tests ──
print("\n" + "=" * 60)
print("SECTION 2: Configuration Functions")
print("=" * 60)

# set_platform_enabled
core.set_platform_enabled(mcmod=True, modrinth=True, wiki=True, wiki_zh=True)
check("set_platform_enabled executed without error", True)

# set_cache
core.set_cache(True, ttl=3600)
check("set_cache(True) executed without error", True)

core.set_cache(False)
check("set_cache(False) executed without error", True)

# Reset cache state for rest of tests
core.set_cache(True, ttl=3600)

# ── 3. search_mcmod() — MC百科搜索 ───────────────────
print("\n" + "=" * 60)
print("SECTION 3: search_mcmod() — MC百科搜索")
print("=" * 60)

try:
    mcmod_results = core.search_mcmod("机械动力", max_results=3, content_type="mod")
    check("search_mcmod returns list", isinstance(mcmod_results, list))
    check("search_mcmod returns >0 results", len(mcmod_results) > 0)

    if mcmod_results:
        r = mcmod_results[0]
        check("Result has 'name' field", 'name' in r)
        check("Result has 'name_zh' field", 'name_zh' in r)
        check("Result has 'name_en' field", 'name_en' in r)
        check("Result has 'url' field", 'url' in r)
        check("Result has 'source' field", 'source' in r)
        check("Result source is mcmod.cn",
              r.get('source') == 'mcmod.cn', str(r.get('source')))
        check("Result url starts with https://www.mcmod.cn",
              r.get('url', '').startswith('https://www.mcmod.cn'),
              r.get('url', ''))
        # name_zh should be non-empty
        check("Result name_zh is non-empty",
              bool(r.get('name_zh', '').strip()),
              repr(r.get('name_zh', '')))
        check("Result has 'source_id' field",
              'source_id' in r and bool(r['source_id']),
              repr(r.get('source_id', '')))

        # Check for description or snippet
        has_desc = bool(r.get('description') and r['description'].strip())
        has_snippet = bool(r.get('snippet') and r['snippet'].strip())
        check("Result has description or snippet",
              has_desc or has_snippet)

    # Print status
    names_found = [r.get('name', '?') for r in mcmod_results[:5]]
    print(f"  >> Found names: {names_found}")

except core.SearchError as e:
    check(f"search_mcmod raised SearchError: {e}", False, str(e))
except Exception as e:
    check(f"search_mcmod raised unexpected exception: {type(e).__name__}", False, str(e))

# ── 4. search_mcmod() — item content_type ────────────
print("\n" + "=" * 60)
print("SECTION 4: search_mcmod() — item search")
print("=" * 60)

try:
    item_results = core.search_mcmod("钻石剑", max_results=3, content_type="item")
    check("search_mcmod(item) returns list", isinstance(item_results, list))
    check("search_mcmod(item) returns >0 results", len(item_results) > 0)

    if item_results:
        r = item_results[0]
        check("Item result has 'name' field", 'name' in r)
        check("Item result has 'url' field", 'url' in r)
        check("Item result url contains /item/",
              '/item/' in r.get('url', ''),
              r.get('url', ''))

    names_found = [r.get('name', '?') for r in item_results[:5]]
    print(f"  >> Item names: {names_found}")

except core.SearchError as e:
    check(f"search_mcmod(item) raised SearchError: {e}", False, str(e))
except Exception as e:
    check(f"search_mcmod(item) raised unexpected exception: {type(e).__name__}", False, str(e))

# ── 5. _parse_mcmod_search_results() — parser test ───
print("\n" + "=" * 60)
print("SECTION 5: _parse_mcmod_search_results() — Parser")
print("=" * 60)

try:
    # We need raw HTML. Fetch it directly via curl.
    search_html = core.curl("https://search.mcmod.cn/s?key=%E6%9C%BA%E6%A2%B0%E5%8A%A8%E5%8A%9B&filter=0")
    check("curl returned search HTML", bool(search_html) and len(search_html) > core.MIN_HTML_LEN,
          f"HTML length: {len(search_html) if search_html else 0}")

    if search_html and len(search_html) > 500:
        # Test parser
        pairs = core._parse_mcmod_search_results(search_html, "mod", "机械动力")
        check("_parse_mcmod_search_results returns list of tuples",
              isinstance(pairs, list) and len(pairs) > 0)
        if pairs:
            url, name = pairs[0]
            check("Parsed pair is (url, name) tuple",
                  isinstance(url, str) and isinstance(name, str) and url.startswith('https://'),
                  f"url={url!r}, name={name!r}")
            check("Parsed URL is mcmod.cn class/ URL",
                  '/class/' in url or '/item/' in url,
                  url)

        # Test metadata extraction
        meta = core._extract_search_result_metadata(search_html)
        check("_extract_search_result_metadata returns dict",
              isinstance(meta, dict))
        # Should have entries
        check("Metadata has entries", len(meta) > 0,
              f"keys: {list(meta.keys())[:3]}")
        if meta:
            sample_url = list(meta.keys())[0]
            sample_meta = meta[sample_url]
            check("Metadata entry has description key",
                  'description' in sample_meta,
                  str(list(sample_meta.keys())))

except Exception as e:
    check(f"Parser tests raised exception: {type(e).__name__}", False, str(e))

# ── 6. search_all() with fuse=True ───────────────────
print("\n" + "=" * 60)
print("SECTION 6: search_all() — aggregate search")
print("=" * 60)

# Test 6a: mod search (mod)
try:
    result = core.search_all("机械动力", max_per_source=3, content_type="mod", fuse=True)
    check("search_all returns dict", isinstance(result, dict))
    check("search_all has 'results' key", 'results' in result)
    check("search_all has 'platform_stats' key", 'platform_stats' in result)
    check("search_all results is list", isinstance(result['results'], list))

    if result['results']:
        r = result['results'][0]
        check("Fused result has 'name'", 'name' in r)
        check("Fused result has '_sources'", '_sources' in r)
        check("Fused result has 'is_primary'", 'is_primary' in r)
        check("Fused result has 'source'", 'source' in r)
        check("is_primary is bool",
              isinstance(r['is_primary'], bool),
              repr(r['is_primary']))
        check("_sources is list",
              isinstance(r['_sources'], list) and len(r['_sources']) > 0,
              str(r['_sources']))

    stats = result.get('platform_stats', {})
    check("platform_stats has mcmod.cn", 'mcmod.cn' in stats)
    check("platform_stats has modrinth", 'modrinth' in stats)

    total_returned = sum(s.get('returned', 0) for s in stats.values())
    check("platform_stats has returned items", total_returned >= 0,
          f"total returned: {total_returned}")

    names = [r.get('name', '?') for r in result['results'][:5]]
    print(f"  >> Fused results ({len(result['results'])} total): {names}")
    print(f"  >> Stats: {json.dumps(stats, ensure_ascii=False)}")

except Exception as e:
    check(f"search_all (fuse=True) exception: {type(e).__name__}", False, str(e))

# Test 6b: English keyword
try:
    result_en = core.search_all("sodium", max_per_source=3, content_type="mod", fuse=True)
    check("search_all (en keyword) returns dict", isinstance(result_en, dict))
    check("search_all (en) has results", len(result_en.get('results', [])) > 0)

    names_en = [r.get('name', '?') for r in result_en['results'][:5]]
    print(f"  >> EN fused results: {names_en}")

except Exception as e:
    check(f"search_all (EN keyword) exception: {type(e).__name__}", False, str(e))

# Test 6c: Empty keyword
try:
    result_empty = core.search_all("", max_per_source=3, content_type="mod", fuse=True)
    check("search_all (empty kw) returns empty results",
          result_empty == {"results": [], "platform_stats": {}},
          str(result_empty)[:200])

except Exception as e:
    check(f"search_all (empty kw) exception: {type(e).__name__}", False, str(e))

# Test 6d: vanilla content_type
try:
    result_vanilla = core.search_all("ender dragon", max_per_source=2, content_type="vanilla", fuse=True)
    check("search_all (vanilla) returns dict", isinstance(result_vanilla, dict))
    check("search_all (vanilla) has results",
          len(result_vanilla.get('results', [])) > 0,
          str(result_vanilla.get('results', [])))

    names_v = [r.get('name', '?') for r in result_vanilla['results'][:3]]
    print(f"  >> Vanilla results: {names_v}")

except Exception as e:
    check(f"search_all (vanilla) exception: {type(e).__name__}", False, str(e))

# ── 7. search_modrinth() ────────────────────────────
print("\n" + "=" * 60)
print("SECTION 7: search_modrinth()")
print("=" * 60)

try:
    mr_result = core.search_modrinth("sodium", max_results=3, project_type="mod")
    check("search_modrinth returns dict", isinstance(mr_result, dict))
    check("search_modrinth has 'results' key", 'results' in mr_result)
    check("search_modrinth has 'total' key", 'total' in mr_result)
    check("search_modrinth has 'returned' key", 'returned' in mr_result)
    check("search_modrinth returns >0 results", len(mr_result.get('results', [])) > 0)

    if mr_result['results']:
        r = mr_result['results'][0]
        check("Modrinth result has 'name'", 'name' in r)
        check("Modrinth result has 'source'", 'source' in r)
        check("Modrinth result has 'source_id'", 'source_id' in r)
        check("Modrinth source is 'modrinth'",
              r.get('source') == 'modrinth',
              repr(r.get('source')))
        check("Modrinth result has 'url'", 'url' in r,
              repr(r.get('url', '')))

    print(f"  >> Modrinth total: {mr_result.get('total')}, returned: {mr_result.get('returned')}")

except Exception as e:
    check(f"search_modrinth exception: {type(e).__name__}", False, str(e))

# ── 8. Wiki search ───────────────────────────────────
print("\n" + "=" * 60)
print("SECTION 8: Wiki Search")
print("=" * 60)

# Wiki EN
try:
    wiki_pages = core.search_wiki("enchanting", max_results=3)
    check("search_wiki returns list", isinstance(wiki_pages, list))
    check("search_wiki returns >0 results", len(wiki_pages) > 0)

    if wiki_pages:
        r = wiki_pages[0]
        check("Wiki result has 'name'", 'name' in r)
        check("Wiki result has 'url'", 'url' in r)
        check("Wiki result has 'snippet'", 'snippet' in r)
        check("Wiki url contains minecraft.wiki",
              'minecraft.wiki' in r.get('url', ''),
              r.get('url', ''))

    names_w = [r.get('name', '?') for r in wiki_pages[:3]]
    print(f"  >> Wiki pages: {names_w}")

except Exception as e:
    check(f"search_wiki exception: {type(e).__name__}", False, str(e))

# Wiki ZH
try:
    wiki_zh_pages = core.search_wiki_zh("附魔", max_results=3)
    check("search_wiki_zh returns list", isinstance(wiki_zh_pages, list))
    check("search_wiki_zh returns >0 results", len(wiki_zh_pages) > 0)

    if wiki_zh_pages:
        r = wiki_zh_pages[0]
        check("Wiki ZH result has 'name'", 'name' in r)
        check("Wiki ZH result has 'url'", 'url' in r)

    names_wz = [r.get('name', '?') for r in wiki_zh_pages[:3]]
    print(f"  >> Wiki ZH pages: {names_wz}")

except Exception as e:
    check(f"search_wiki_zh exception: {type(e).__name__}", False, str(e))

# ── 9. Cache Functionality ──────────────────────────
print("\n" + "=" * 60)
print("SECTION 9: Cache Functionality")
print("=" * 60)

# Enable cache for tests
core.set_cache(True, ttl=3600)

# First call
t0 = time.time()
r1 = core.search_mcmod("机械动力", max_results=2, content_type="mod")
t1 = time.time()
first_call_time = t1 - t0
check("First call with cache returns results", len(r1) > 0,
      f"took {first_call_time:.2f}s")

# Second call (should be cached)
t0 = time.time()
r2 = core.search_mcmod("机械动力", max_results=2, content_type="mod")
t1 = time.time()
second_call_time = t1 - t0
check("Second call (cached) returns results", len(r2) > 0,
      f"took {second_call_time:.2f}s")

# Verify cache directory exists
import tempfile
cache_dir = core.Path.home() / ".cache" / "mc-search"
check("Cache directory exists", cache_dir.exists())

print(f"  >> First call: {first_call_time:.2f}s, Second call: {second_call_time:.2f}s")

# Disable cache
core.set_cache(False)

# ── 10. Error Handling ───────────────────────────────
print("\n" + "=" * 60)
print("SECTION 10: Error Handling")
print("=" * 60)

# _is_valid helper check
def _is_valid(info):
    return info is not None and not (isinstance(info, dict) and '_error' in info)

check("_is_valid(None) = False", not _is_valid(None))
check("_is_valid({'_error': 'not_found'}) = False",
      not _is_valid({'_error': 'not_found'}))
check("_is_valid({'name': 'foo'}) = True",
      _is_valid({'name': 'foo'}))
check("_is_valid({}) = True",
      _is_valid({}))

# Test: search_all with invalid content_type
try:
    r_invalid = core.search_all("test", max_per_source=1, content_type="invalid_type", fuse=True)
    check("search_all with invalid content_type returns results dict",
          isinstance(r_invalid, dict) and 'results' in r_invalid)
except Exception as e:
    check(f"search_all invalid content_type exception: {type(e).__name__}", False, str(e))

# Test: fetch_mod_info with non-existent mod
try:
    info_none = core.fetch_mod_info("this_mod_does_not_exist_xyz")
    check("fetch_mod_info(non_existent) returns None or error",
          info_none is None or (isinstance(info_none, dict) and '_error' in info_none))
except Exception as e:
    check(f"fetch_mod_info(non_existent) exception: {type(e).__name__}", False, str(e))

# ── 11. search_all with fuse=False (platform separation) ──
print("\n" + "=" * 60)
print("SECTION 11: search_all with fuse=False")
print("=" * 60)

try:
    result_raw = core.search_all("机械动力", max_per_source=2, content_type="mod", fuse=False)
    check("search_all(fuse=False) returns dict", isinstance(result_raw, dict))
    check("fuse=False has mcmod.cn key", 'mcmod.cn' in result_raw)
    check("fuse=False mcmod.cn is list", isinstance(result_raw.get('mcmod.cn'), list))
    check("fuse=False has modrinth key", 'modrinth' in result_raw)
    check("fuse=False modrinth is list or dict", isinstance(result_raw.get('modrinth'), (list, dict)))

    print(f"  >> mcmod.cn: {len(result_raw.get('mcmod.cn', []))} results")
    if isinstance(result_raw.get('modrinth'), dict):
        print(f"  >> modrinth: {len(result_raw.get('modrinth', {}).get('results', []))} results")
    else:
        print(f"  >> modrinth: {len(result_raw.get('modrinth', []))} results")

except Exception as e:
    check(f"search_all(fuse=False) exception: {type(e).__name__}", False, str(e))

# ── 12. Author Search ────────────────────────────────
print("\n" + "=" * 60)
print("SECTION 12: Author Search")
print("=" * 60)

try:
    author_mods = core.search_mcmod_author("Simibubi", max_mods=5)
    check("search_mcmod_author returns list", isinstance(author_mods, list))
    check("search_mcmod_author returns >0 results", len(author_mods) > 0)
    if author_mods:
        check("Author result has 'name'", 'name' in author_mods[0])
        check("Author result has 'source'", 'source' in author_mods[0])
    print(f"  >> Author mods: {[m.get('name', '?') for m in author_mods[:3]]}")

except core.SearchError as e:
    check(f"search_mcmod_author raised SearchError: {e}", False, str(e))
except Exception as e:
    check(f"search_mcmod_author exception: {type(e).__name__}", False, str(e))

# ── 13. Modrinth Author Search ──────────────────────
print("\n" + "=" * 60)
print("SECTION 13: Modrinth Author Search")
print("=" * 60)

try:
    mr_author = core.search_modrinth_author("jellysquid3", max_results=5)
    check("search_modrinth_author returns list", isinstance(mr_author, list))
    check("search_modrinth_author returns >0 results", len(mr_author) > 0)
    if mr_author:
        check("MR author result has 'name'", 'name' in mr_author[0])
        check("MR author result has 'source'", 'source' in mr_author[0])
    print(f"  >> MR author mods: {[m.get('name', '?') for m in mr_author[:3]]}")

except Exception as e:
    check(f"search_modrinth_author exception: {type(e).__name__}", False, str(e))

# ── Summary ─────────────────────────────────────────
print("\n" + "=" * 60)
print("REGRESSION TEST SUMMARY")
print("=" * 60)
total = passed + failed + skipped
print(f"  Total:  {total}")
print(f"  Passed: {passed}")
print(f"  Failed: {failed}")
print(f"  Skipped:{skipped}")
print("=" * 60)

if failed > 0:
    print("\n*** SOME TESTS FAILED ***")
    sys.exit(1)
else:
    print("\nAll tests passed!")
    sys.exit(0)