#!/usr/bin/env python3
"""Emit JS that extracts rendered XHS search feeds.

Adapted from browser-act/skills xiaohongshu-search-full under the MIT license.
"""

import argparse
import json
import sys


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()
    limit_js = max(1, args.limit)
    js = f"""
(function() {{
  try {{
    var search = window.__INITIAL_STATE__ && window.__INITIAL_STATE__.search;
    if (!search) return JSON.stringify({{error:true,message:'__INITIAL_STATE__.search not found'}});
    function unwrap(v) {{ return (v && v._value !== undefined) ? v._value : v; }}
    function deep(obj, depth) {{
      if (depth > 5 || obj === null || obj === undefined) return obj;
      obj = unwrap(obj);
      if (typeof obj !== 'object') return obj;
      if (Array.isArray(obj)) return obj.map(function(x) {{ return deep(x, depth + 1); }});
      var out = {{}};
      Object.keys(obj).forEach(function(k) {{ out[k] = deep(obj[k], depth + 1); }});
      return out;
    }}
    var feeds = deep(search.feeds, 0);
    var hasMore = deep(search.hasMore, 0);
    if (!Array.isArray(feeds)) return JSON.stringify({{error:true,message:'feeds is not an array'}});
    var items = [];
    for (var i = 0; i < feeds.length && items.length < {limit_js}; i++) {{
      var item = feeds[i];
      if (!item || item.modelType !== 'note') continue;
      var card = item.noteCard || {{}};
      var user = card.user || {{}};
      var interact = card.interactInfo || {{}};
      var noteId = item.id || item.trackId || '';
      var token = item.xsecToken || '';
      var pubDate = '';
      var tags = card.cornerTagInfo;
      if (tags) {{
        var tagArray = Array.isArray(tags) ? tags : Object.values(tags);
        for (var j = 0; j < tagArray.length; j++) {{
          if (tagArray[j] && tagArray[j].type === 'publish_time') {{ pubDate = tagArray[j].text || ''; break; }}
        }}
      }}
      items.push({{
        id: noteId,
        xsec_token: token,
        note_url: noteId ? 'https://www.xiaohongshu.com/explore/' + noteId + '?xsec_token=' + encodeURIComponent(token) + '&xsec_source=pc_search' : '',
        type: card.type || '',
        title: card.displayTitle || '',
        publish_date: pubDate,
        liked_count: interact.likedCount || '0',
        collected_count: interact.collectedCount || '0',
        comment_count: interact.commentCount || '0',
        shared_count: interact.sharedCount || '0',
        author_nickname: user.nickname || user.nickName || ''
      }});
    }}
    return JSON.stringify({{total_count:items.length,has_more:!!hasMore,items:items}});
  }} catch(e) {{ return JSON.stringify({{error:true,message:e.message}}); }}
}})()
"""
    print(js)


if __name__ == "__main__":
    main()

