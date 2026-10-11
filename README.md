# Footage Manager

A local media library for quickly finding scenes, dialogue, and sounds in your source footage. Original files are neither copied nor modified.

## Features

- Automatic folder monitoring and a resumable processing queue.
- PySceneDetect scene detection and lightweight 720p previews.
- Full-text search across filenames, tags, and transcripts.
- Optional local AI search for images and audio.
- English and Russian search queries.
- Built-in playback, manual tags, and fast or frame-accurate clip export.
- Local SQLite and LanceDB storage, without uploading source footage to the cloud.

## Installation on Windows + WSL

Requires WSL2 Ubuntu, Python 3.11/3.12, and Node.js 20+. Run in WSL:

```bash
sudo apt update
sudo apt install -y python3-venv curl xz-utils
```

Then run `install-footage-manager.cmd`. If FFmpeg is missing, the installer downloads a local build into the project folder. For the full AI installation, run this in WSL instead:

```bash
./scripts/setup-wsl.sh --with-ai
```

The AI installation is large: it installs CUDA-compatible PyTorch, WhisperX, SigLIP 2, CLAP, OPUS-MT, and LanceDB. Models download on first use and subsequently run from the local cache.

After installation, run `start-footage-manager.cmd`; the application opens at `http://127.0.0.1:8787`.

## Development

```bash
./scripts/dev.sh
```

Backend API documentation: `http://127.0.0.1:8787/docs`. Development interface: `http://127.0.0.1:5173`.

## Configuration

Settings use environment variables with the `FOOTAGE_` prefix:

- `FOOTAGE_DATA_DIR`: database, cache, and indexes.
- `FOOTAGE_EXPORT_DIR`: exported clips.
- `FOOTAGE_SCAN_INTERVAL_SECONDS`: folder scan interval in seconds; defaults to 60.
- `FOOTAGE_SEMANTIC_SEARCH_ENABLED` and `FOOTAGE_TRANSCRIPTION_ENABLED`: enable the resource-intensive AI stages.

## Model licenses

- Google SigLIP 2: Apache 2.0.
- LAION CLAP General: Apache 2.0.
- Helsinki-NLP OPUS-MT ru→en: CC BY 4.0.
- WhisperX and the selected Whisper models: see the respective project licenses.

The application does not verify usage rights for indexed footage. The library owner remains responsible for how the material is used.

## Vehicles in videos: tracks and best frames

In **Vehicles**, select a video and click **Find vehicles**. **Reprocess tracks** starts a new processing run for a previously processed video. Previous results and labels remain in **Previous runs**. When timestamps and bounding boxes match, custom labels carry over to the new tracks. The source video is not modified.

New videos automatically enter the vehicle tracking queue after regular indexing.

Install the dependencies:

```bash
backend/.venv/bin/pip install -r backend/requirements-vehicles.txt
```

The YOLO11n detector and ByteTrack are provided by Ultralytics under AGPL-3.0. Model weights download on first use; subsequent processing is local. Vehicle makes and models are not identified, and labels are not used for training.

### How processing works

- PyAV decodes the source sequentially. Timestamps come from actual frame PTS values, adjusted for the video stream start. Variable-frame-rate videos are supported.
- By default, the detector analyzes up to 5 frames per second (200 ms intervals), with a 640-pixel input, on the CPU. ByteTrack uses a Kalman filter, global matching, and low-confidence detections to maintain tracks. Tracks survive gaps of up to 2 seconds. The tracker resets at scene boundaries.
- One best result is stored per confirmed ByteTrack track. Unconfirmed, one-off candidates are not published. The quality score weights relative size at 25%, confidence at 20%, normalized sharpness (Laplacian variance) at 25%, edge clearance at 15%, and lack of occlusion at 15%. All components are stored in the metadata.
- Occlusion is an approximate estimate of the union of overlaps with other detected objects. The detector has no depth information, so estimates may be wrong and some overlaps may go undetected.
- Each result saves a crop with 12.5% padding on each side of the bounding box, clipped to the image boundaries, and a full-resolution frame. Both are JPEGs at quality 95, without a drawn bounding box. Bounding box coordinates come from the detector, rather than tracker predictions.

Each result includes the source video, timestamp in milliseconds, a unique `track_id` (including run and scene identifiers), observation start/end times, confidence, bounding box, padded crop coordinates, crop, full frame, custom label, and quality scores.

**Original frame** opens the full frame. **View in video** opens the video at the selected timestamp. ZIP exports include crops, full frames, and a CSV containing all of this metadata.

### Possible matches

Similar tracks from different scenes of the same video are grouped as **Possibly the same vehicle**. Matching conservatively compares perceptual hashes, color histograms, and crop proportions. Each result in a group must match every other result; simultaneous vehicles from the same scene are not grouped.

This is a visual similarity heuristic, not a confirmed identity: different angles may fail to match, while different vehicles with similar appearances may match. Grouping does not delete tracks or files. Clicking the group button displays all of its results.

### Settings

- `FOOTAGE_VEHICLE_DETECTION_ENABLED=false`: disable automatic processing for new videos.
- `FOOTAGE_VEHICLE_TRACK_SAMPLE_MS=200`: analysis interval. Increasing it reduces resource usage but makes fast motion harder to track.
- `FOOTAGE_VEHICLE_TRACK_GAP_MS=2000`: how long a lost track is retained.
- `FOOTAGE_VEHICLE_CROP_PADDING=0.125`: crop padding, constrained to 10–15%.
- `FOOTAGE_VEHICLE_CONFIDENCE=0.45`: threshold for starting a track. Low-confidence detections above 0.1 are used only to continue existing tracks.

Files are stored in `FOOTAGE_DATA_DIR/cache/<video-id>/vehicles/<run-id>/`.

Failed or interrupted runs preserve previous results and remove incomplete new files. Previous successful runs remain in history along with their files.
