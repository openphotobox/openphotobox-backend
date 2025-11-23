#!/usr/bin/env python
"""
Convenience script to start a Celery worker.

Examples:
  # Metadata worker (parallel EXIF/XMP parsing)
  python start_worker.py --queues metadata --pool threads --concurrency 6

  # AI worker (face detection + CLIP)
  python start_worker.py --queues ai --pool threads --concurrency 2

Notes:
  - threads pool avoids historical segfaults some libs had with prefork.
  - Adjust concurrency based on CPU/GPU and DB/Redis capacity.
"""

import os
import sys
import argparse
import django
from pathlib import Path

# Add the backend directory to Python path
backend_dir = Path(__file__).parent
sys.path.insert(0, str(backend_dir))

# Set up Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'openphotobox_backend.settings')
django.setup()

# Import and start Celery
from openphotobox_backend.celery import app


def main():
    parser = argparse.ArgumentParser(description='Start a Celery worker')
    parser.add_argument('--queues', default=os.environ.get('CELERY_QUEUES', 'metadata,ai,celery'), help='Comma-separated list of queues to consume')
    parser.add_argument('--concurrency', type=int, default=int(os.environ.get('CELERY_CONCURRENCY', '4')), help='Number of concurrent worker threads/processes')
    parser.add_argument('--pool', default=os.environ.get('CELERY_POOL', 'threads'), choices=['prefork', 'solo', 'threads', 'gevent', 'eventlet'], help='Worker pool implementation')
    parser.add_argument('--loglevel', default=os.environ.get('CELERY_LOGLEVEL', 'info'), help='Logging level')
    parser.add_argument('--preset', choices=['metadata', 'ai'], help='Apply a recommended preset for the worker')

    args = parser.parse_args()

    if args.preset == 'metadata':
        # CPU-light parsing, higher parallelism
        args.queues = 'metadata'
        if 'CELERY_CONCURRENCY' not in os.environ:
            args.concurrency = 6
        if 'CELERY_POOL' not in os.environ:
            args.pool = 'threads'
    elif args.preset == 'ai':
        # InsightFace/CLIP work, keep modest parallelism
        args.queues = 'ai'
        if 'CELERY_CONCURRENCY' not in os.environ:
            args.concurrency = 2
        if 'CELERY_POOL' not in os.environ:
            args.pool = 'threads'

    print("Starting Celery worker...")
    print(f"  Queues      : {args.queues}")
    print(f"  Pool        : {args.pool}")
    print(f"  Concurrency : {args.concurrency}")
    print(f"  Log level   : {args.loglevel}")
    print("Make sure Redis is running on localhost:6379")
    print("Press Ctrl+C to stop the worker")
    print("-" * 50)

    app.worker_main([
        'worker',
        f'--loglevel={args.loglevel}',
        f'--queues={args.queues}',
        f'--concurrency={args.concurrency}',
        f'--pool={args.pool}',
    ])


if __name__ == '__main__':
    main()
