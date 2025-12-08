LIKE_COLORS = [  # greens
    ("#0F9D58", "#57D282"),
    ("#22B573", "#75DAA4"),
    ("#34C759", "#8EE5B5"),
    ("#4ADE80", "#A7F3CE"),
]

DISLIKE_COLORS = [  # reds
    ("#C81E1E", "#F87272"),
    ("#D74646", "#F78C8C"),
    ("#E25D5D", "#F9A8A8"),
    ("#EF7E7E", "#FBCACA"),
]

def assign_feedback_palette(configs):
    """
    Produce a color palette for like/dislike feedback grouped by config name.
    """
    palette = []
    for idx, cfg in enumerate(configs or []):
        name = cfg.get("name") or f"config_{idx + 1}"
        like_colors = LIKE_COLORS[idx % len(LIKE_COLORS)]
        dislike_colors = DISLIKE_COLORS[idx % len(DISLIKE_COLORS)]
        palette.append(
            {
                "name": name,
                "like_with_config": like_colors[0],
                "like_no_config": like_colors[1],
                "dislike_with_config": dislike_colors[0],
                "dislike_no_config": dislike_colors[1],
            }
        )
    return palette
