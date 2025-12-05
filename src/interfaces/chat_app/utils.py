def collapse_assistant_sequences(history_rows, sender_name=None, sender_index=0):
    """
    Keep only the latest assistant response within any contiguous block.
    Works for both simple history tuples and extended rows with metadata.
    """
    if not history_rows:
        return history_rows

    collapsed = []
    assistant_run = []
    for row in history_rows:
        sender = row[sender_index]
        if sender == sender_name:
            assistant_run.append(row)
        else:
            if assistant_run:
                collapsed.append(assistant_run[-1])
                assistant_run = []
            collapsed.append(row)

    if assistant_run:
        collapsed.append(assistant_run[-1])

    return collapsed
