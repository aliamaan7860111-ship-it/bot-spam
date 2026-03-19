import { Client } from "@notionhq/client";

if (!process.env.NOTION_API_KEY) {
    console.warn("Missing NOTION_API_KEY environment variable");
}

export const notion = new Client({
    auth: process.env.NOTION_API_KEY,
});

export const NOTION_DATABASE_ID = process.env.NOTION_DATABASE_ID;
