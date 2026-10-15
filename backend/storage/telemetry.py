"""Bounded telemetry batches share the configured DB and one transaction."""
import json
import math
from .database import connect


def write_batch(session_id,user_id,poses,sensors):
    if not 1<=len(poses)+len(sensors)<=1000:raise ValueError('Batch requires 1 to 1000 records')
    def finite(value):
        if isinstance(value,float) and not math.isfinite(value):raise ValueError('Telemetry numbers must be finite')
        if isinstance(value,dict):
            for item in value.values():finite(item)
        elif isinstance(value,(list,tuple)):
            for item in value:finite(item)
    for record in [*poses,*sensors]:finite(record.model_dump())
    conn=connect()
    try:
        row=conn.execute('SELECT user_id FROM sessions WHERE session_id=?',(session_id,)).fetchone()
        if not row or str(row[0])!=str(user_id):raise PermissionError('Session owner mismatch')
        conn.executemany('INSERT INTO pose_data(session_id,timestamp,exercise_type,rep_count,form_score,angles_json,landmarks_json) VALUES (?,?,?,?,?,?,?)',[(session_id,p.timestamp,p.exercise_type,p.rep_count,p.form_score,json.dumps(p.angles,allow_nan=False),json.dumps(p.landmarks,allow_nan=False)) for p in poses])
        conn.executemany('INSERT INTO sensor_data(session_id,timestamp,heart_rate,hr_confidence,movement_intensity,body_angle,accel_x,accel_y,accel_z) VALUES (?,?,?,?,?,?,?,?,?)',[(session_id,p.timestamp,p.heart_rate,p.hr_confidence,p.movement_intensity,p.body_angle,p.accel_x,p.accel_y,p.accel_z) for p in sensors])
        conn.commit()
        return {'poses':len(poses),'sensors':len(sensors)}
    except Exception:conn.rollback();raise
    finally:conn.close()
