#!/usr/bin/env python3
"""Emit JS that extracts an XHS note from rendered SSR state.

Adapted from browser-act/skills xiaohongshu-search-full under the MIT license.
Used only to refresh or diagnose MediaCrawler detail failures.
"""

import argparse
import json
import sys


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser()
    parser.add_argument("note_id")
    args = parser.parse_args()
    note_id = json.dumps(args.note_id)
    js = f"""
(function() {{
  try {{
    var noteId = {note_id};
    var noteMap = window.__INITIAL_STATE__ && window.__INITIAL_STATE__.note && window.__INITIAL_STATE__.note.noteDetailMap;
    if (!noteMap) return JSON.stringify({{error:true,message:'noteDetailMap not found'}});
    var ref = noteMap[noteId];
    if (!ref) return JSON.stringify({{error:true,message:'note not found in state'}});
    var container = ref._value || ref;
    var noteRef = container.note;
    var note = noteRef && noteRef._value ? noteRef._value : noteRef;
    if (!note) return JSON.stringify({{error:true,message:'note data missing'}});
    var interact = note.interactInfo || {{}};
    return JSON.stringify({{
      noteId: note.noteId,
      title: note.title || '',
      desc: note.desc || '',
      type: note.type || '',
      time: note.time || null,
      nickname: note.user && note.user.nickname || '',
      likedCount: interact.likedCount || null,
      collectedCount: interact.collectedCount || null,
      commentCount: interact.commentCount || null,
      shareCount: interact.shareCount || null,
      tagList: (note.tagList || []).map(function(t) {{ return {{name:t.name,type:t.type}}; }}),
      imageCount: (note.imageList || []).length
    }});
  }} catch(e) {{ return JSON.stringify({{error:true,message:e.message}}); }}
}})()
"""
    print(js)


if __name__ == "__main__":
    main()

