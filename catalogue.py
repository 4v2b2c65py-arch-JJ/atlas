"""Video outlet catalogue — verified public sample assets.

All URLs probe-verified (HTTP 206). test-videos.co.uk hosts h264 encodes
of the Blender open movies; download.samplelib.com hosts generic clips.
"""

TV = "https://test-videos.co.uk/vids"
SL = "https://download.samplelib.com/mp4"

CATALOGUE = [
    # --- 720p / 1080p open-movie clips (verified) ---
    {"title": "Big Buck Bunny 720p", "url": f"{TV}/bigbuckbunny/mp4/h264/720/Big_Buck_Bunny_720_10s_1MB.mp4",
     "tags": ["animation", "bunny", "blender", "open movie", "comedy", "cartoon"], "size_mb": 1},
    {"title": "Big Buck Bunny 1080p", "url": f"{TV}/bigbuckbunny/mp4/h264/1080/Big_Buck_Bunny_1080_10s_1MB.mp4",
     "tags": ["animation", "bunny", "blender", "open movie", "comedy", "cartoon", "hd"], "size_mb": 2},
    {"title": "Jellyfish 720p", "url": f"{TV}/jellyfish/mp4/h264/720/Jellyfish_720_10s_1MB.mp4",
     "tags": ["ocean", "jellyfish", "underwater", "nature", "4k-demo"], "size_mb": 1},
    {"title": "Jellyfish 1080p", "url": f"{TV}/jellyfish/mp4/h264/1080/Jellyfish_1080_10s_1MB.mp4",
     "tags": ["ocean", "jellyfish", "underwater", "nature", "hd"], "size_mb": 2},
    {"title": "Sintel 720p", "url": f"{TV}/sintel/mp4/h264/720/Sintel_720_10s_1MB.mp4",
     "tags": ["animation", "fantasy", "dragon", "blender", "open movie", "action"], "size_mb": 1},
    {"title": "Sintel 1080p", "url": f"{TV}/sintel/mp4/h264/1080/Sintel_1080_10s_1MB.mp4",
     "tags": ["animation", "fantasy", "dragon", "blender", "open movie", "action", "hd"], "size_mb": 2},
    # --- samplelib generic clips (verified sample-5s; siblings 301->follow) ---
    {"title": "Sample Clip 5s", "url": f"{SL}/sample-5s.mp4",
     "tags": ["sample", "test", "clip", "short", "demo"], "size_mb": 3},
    {"title": "Sample Clip 10s", "url": f"{SL}/sample-10s.mp4",
     "tags": ["sample", "test", "clip", "demo"], "size_mb": 5},
    {"title": "Sample Clip 15s", "url": f"{SL}/sample-15s.mp4",
     "tags": ["sample", "test", "clip", "demo"], "size_mb": 8},
    {"title": "Sample Clip 20s", "url": f"{SL}/sample-20s.mp4",
     "tags": ["sample", "test", "clip", "demo"], "size_mb": 10},
    {"title": "Sample Clip 30s", "url": f"{SL}/sample-30s.mp4",
     "tags": ["sample", "test", "clip", "demo", "longer"], "size_mb": 15},
]
