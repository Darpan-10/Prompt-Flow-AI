# Module 1: Auth & Access Control — Setup & Quickstart Guide

## 📋 Prerequisites (Arch Linux)

### Install System Packages
```bash
# PostgreSQL 15
sudo pacman -S postgresql

# Redis
sudo pacman -S redis

# Python 3.11+
sudo pacman -S python python-pip

# Docker (optional, for containerized deployment)
sudo pacman -S docker docker-compose
```

### Initialize PostgreSQL
```bash
# As root, initialize the database cluster
sudo -u postgres initdb -D /var/lib/postgres/data

# Start PostgreSQL service
sudo systemctl start postgresql
sudo systemctl enable postgresql

# Create promptflow database and user
sudo -u postgres psql << EOF
CREATE USER promptflow WITH PASSWORD 'secret';
CREATE DATABASE promptflow OWNER promptflow;
GRANT ALL PRIVILEGES ON DATABASE promptflow TO promptflow;
