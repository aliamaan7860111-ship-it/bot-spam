import json
import logging

def build_loading_block(user_id, prompt):
    """Returns a simple Block Kit message acknowledging the request."""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"⏳ Thanks <@{user_id}>! **The Antigravity Ad Pipeline** is gathering data, analyzing Google Docs, and building 3 elite ad briefs for:\n> _{prompt}_"
            }
        }
    ]

def build_concepts_blocks(ad_concepts, session_id, style_gallery=None):
    """
    Takes the JSON array from Claude and turns them into a massive Slack UI message.
    Adds [Approve] and [Reject] buttons to the bottom of each concept.
    """
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "🎯 Claude 4.6 Strategy Engine Finished!"
            }
        }
    ]
    
    blocks.append({"type": "divider"})
    
    for i, concept in enumerate(ad_concepts):
        # Fallbacks in case Claude missed a key
        headline = concept.get('headline', 'No Headline')
        subheadline = concept.get('subheadline', '')
        angle = concept.get('ad_angle', 'No Angle')
        visuals = concept.get('visual_direction', 'No Visual Direction')
        # Phase 4 Update: Retrieve the Style Category from the brief
        style = concept.get('style_category', 'Standard').upper()
        
        # We NO LONGER stringify the whole concept. We just send a reference pointer.
        # Format: "session_id|index"
        button_value = f"{session_id}|{i}"
        
        reasoning = concept.get('design_reasoning', 'No reasoning provided.')
        
        text_content = f"*Concept #{i+1}: {headline}*\n"
        if subheadline:
            text_content += f"_{subheadline}_\n"
            
        text_content += f"> *Design Principles:* {reasoning}\n"
        text_content += f"> *Style:* `{style}` | *Angle:* {angle}\n"
        text_content += f"*Creative Direction:* {visuals}"

        concept_block = {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": text_content
            }
        }
        
        button_block = {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "✅ Approve & Generate Image",
                        "emoji": True
                    },
                    "style": "primary",
                    "value": button_value,
                    "action_id": f"approve_concept_{i}"
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "✏️ Refine AI Idea",
                        "emoji": True
                    },
                    "value": button_value,
                    "action_id": f"refine_concept_{i}"
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "❌ Reject",
                        "emoji": True
                    },
                    "style": "danger",
                    "value": "reject",
                    "action_id": f"reject_concept_{i}"
                }
            ]
        }
        
        blocks.append(concept_block)
        blocks.append(button_block)
        blocks.append({"type": "divider"})
        
    return blocks
