"""Optional: fetch a reference song with yt-dlp (Unlicense) for Reference Ears. Needs yt-dlp and ffmpeg.

  python fetch.py "<Artist - Title | YouTube link>" <out_dir>      -> prints JSON {"path", "title", "url"}

A plain "Artist - Title" is searched on YouTube (first result). Only download music you have the right to use for
your own study; set reference_ears.fetch = "files" in config.json to turn downloading off and use local files only.
"""
import json
import os
import sys


def fetch(query, outdir):
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError('yt-dlp is not installed in this Python (pip install yt-dlp; ffmpeg must be on PATH)')
    os.makedirs(outdir, exist_ok=True)
    target = query if query.startswith('http') else 'ytsearch1:' + query
    opts = {'format': 'bestaudio/best', 'outtmpl': os.path.join(outdir, '%(title).100B.%(ext)s'),
            'noplaylist': True, 'quiet': True, 'no_warnings': True,
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'}]}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(target, download=True)
        if 'entries' in info:
            info = info['entries'][0]
        path = os.path.splitext(ydl.prepare_filename(info))[0] + '.mp3'
    if not os.path.exists(path):
        raise RuntimeError('download finished but no MP3 was found (is ffmpeg on PATH?)')
    return {'path': path, 'title': info.get('title') or query, 'url': info.get('webpage_url') or query}


if __name__ == '__main__':
    print(json.dumps(fetch(sys.argv[1], sys.argv[2])))
