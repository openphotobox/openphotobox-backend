# OpenPhotobox Backend

Django REST Framework backend for the OpenPhotobox family photo management system.

## Features

- **Asset Management**: Store and manage photos/videos with S3 storage
- **Face Recognition**: Detect faces and assign them to people
- **Albums**: Organize photos into albums
- **Sharing**: Create public share links for albums or people
- **Search**: CLIP-based semantic search capabilities
- **Upload Batches**: Track and manage bulk uploads

## Development Tooling

This project uses Ruff (lint + format), Mypy (static typing), and Pre-Commit hooks for consistent code quality.

### Setup

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
pre-commit install  # sets up git hooks
```
