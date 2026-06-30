---
tags: [home, moc]
aliases: [Index, Start Here, MOC]
---

# 🏠 Sliderobes Atlas — Knowledge Vault

> Internal operations platform for **order management, stock control, purchasing, scheduling, and reporting**.
> This vault is the living documentation / context for the `stock_taking` Django project.

## 🧭 How to use this vault
- Open this file in [Obsidian](https://obsidian.md) as the home of your vault.
- Notes are interlinked with `[[wikilinks]]`. Use the **Graph View** to explore relationships.
- Add your own notes anywhere — drop meeting notes, decisions, and TODOs into `Inbox/`.

## 🗺️ Maps of Content (MOC)

### Architecture
- [[Project Overview]]
- [[Tech Stack]]
- [[Page Construction]]
- [[URL Routing]]
- [[Database Schema]]
- [[Integrations]]
- [[Middleware & Auth]]
- [[Configuration]]
- [[Deployment]]

### Django Apps
- [[App - stock_take]]
- [[App - material_generator]]

### Domain Knowledge
- [[Data Models]]
- [[Order Lifecycle]]
- [[Financial Flow]]
- [[Workflow System]]
- [[Inventory Management]]

### Features
- [[Feature Map]]

### Working Notes
- [[Inbox/_README|Inbox]]
- [[People & Roles]]
- [[Glossary]]

## ✅ Quick facts
- **Framework:** Django 6.0 · Python 3.12 · PostgreSQL
- **Deploy:** DigitalOcean App Platform · Gunicorn · Traefik
- **Integrations:** Anthill (CRM) · Xero (accounting) · WorkGuru · Microsoft Graph (email) · DigitalOcean Spaces (S3)
- **Apps:** `stock_take` (core) · `material_generator` (PNX/CSV generation)
