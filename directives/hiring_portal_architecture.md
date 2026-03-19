# Directive: Custom Hiring Portal Architecture

## Overview
This document outlines the architecture for the custom hiring portal, specifically detailing how the frontend application connects securely to the Notion database. 

## The Architecture (Option 1: Next.js Serverless Functions)

We will build the frontend using **Next.js**. The connection between the public-facing form and our private Notion database will be handled by **Next.js Serverless API Routes**. This approach ensures security, speed, and a unified codebase.

### 1. The Frontend (Client-Side)
- **Tech Stack:** Next.js (React), Tailwind CSS.
- **Functionality:** A bespoke, branded form where candidates input their details (Name, Email, Links, Screening Answers) and upload their resume.
- **Action:** When the candidate clicks "Submit", the frontend JavaScript packages this data into a JSON payload and sends a `POST` request to our internal API route (e.g., `/api/submit-application`).
- **Security:** The frontend code *never* contains the Notion API key. It only knows how to talk to our own backend route.

### 2. The Middleman (Next.js Serverless API Route)
- **Tech Stack:** Next.js API Routes (Node.js environment).
- **Functionality:** This is the secure bridge. It receives the payload from the frontend.
- **File Upload Handling:** If the payload includes a resume file, the API route will first upload this file to a secure cloud storage solution (like AWS S3 or UploadThing) and retrieve a public URL for the file.
- **Notion Integration:** The API route securely loads the `NOTION_API_KEY` from environment variables (which are hidden from the frontend). It constructs a request payload formatted according to the official Notion API specifications.
- **Action:** The API route sends a `POST` request to the Notion API (`https://api.notion.com/v1/pages`) to create a new page in the Candidates Database.
- **Response:** The API route returns a success or error message back to the frontend, which then updates the UI for the candidate (e.g., "Application Submitted Successfully!").

### 3. The Destination (Notion Database)
- **Action:** Notion receives the secure request from our API route and creates a new row (page) in the designated Candidates Database.
- **Data Mapping:**
  - **Name:** Mapped to the Title property.
  - **Email:** Mapped to the Email property.
  - **Resume:** Mapped to a Files & media or URL property using the link generated in Step 2.
  - **Screening Answers:** Formatted cleanly and inserted into the *body* of the Notion page (the page content blocks).

## Why This Approach?
- **Maximum Security:** The Notion API key never touches the user's browser.
- **Premium Experience:** The entire process happens rapidly within our own branded environment without redirecting the user to a third-party form provider.
- **Maintainability:** The frontend UI and the secure backend bridge live in the exact same GitHub repository and deploy together seamlessly.
