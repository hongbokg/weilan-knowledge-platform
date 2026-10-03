def pick_summary_job(rows,last_model,cooldowns,now):
    eligible=[r for r in rows if cooldowns.get(r['model_id'],0)<=now]
    if not eligible:return None
    alternate=[r for r in eligible if r['model_id'] and r['model_id']!=last_model]
    return (alternate or eligible)[0]
