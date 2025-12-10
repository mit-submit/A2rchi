LIKE_COLORS = [  # greens
    "#0F9D58",
    "#22B573",
    "#34C759",
    "#4ADE80",
]

DISLIKE_COLORS = [  # reds
    "#C81E1E",
    "#D74646",
    "#E25D5D",
    "#EF7E7E",
]

def assign_feedback_palette(configs):
    """
    Produce a color palette for like/dislike feedback grouped by config name.
    """
    palette = []
    for idx, cfg in enumerate(configs or []):
        name = cfg.get("name") or f"config_{idx + 1}"
        palette.append(
            {
                "name": name,
                "like": LIKE_COLORS[idx % len(LIKE_COLORS)],
                "dislike": DISLIKE_COLORS[idx % len(DISLIKE_COLORS)],
            }
        )
    return palette
