import asyncio
import csv
import os
import threading
import time
import tkinter as tk

from bleak import BleakClient, BleakScanner

from src.bike_state import BikeState
from src.ble_server import start_ble_server
from src.config import (
    BAUDRATE,
    HEART_RATE_NAME_HINTS,
    HEART_RATE_UUID,
    SERIAL_PORT,
    USER_AGE,
    USER_WEIGHT_KG,
    VIRTUAL_DRIVE_RATIO,
    VIRTUAL_WHEEL_CIRCUMFERENCE_M,
    ZONE_HIGH_BPM,
    ZONE_LOW_BPM,
)
from src.serial_reader import SerialReader


def calculate_speed_mph(rpm):
    if rpm <= 0:
        return 0.0
    wheel_rpm = rpm * VIRTUAL_DRIVE_RATIO
    meters_per_minute = wheel_rpm * VIRTUAL_WHEEL_CIRCUMFERENCE_M
    return (meters_per_minute * 60) / 1609.344


def serial_worker(reader, state):
    last_valid_time = None

    while True:
        if not reader or not reader.ser or not reader.ser.is_open:
            last_valid_time = None
            time.sleep(1)
            continue

        try:
            if reader.update():
                now = time.time()
                if last_valid_time is not None:
                    dt = now - last_valid_time
                    if 0 < dt <= 2.0 and state.rpm > 0:
                        state.total_wheel_revs += (
                            (state.rpm * VIRTUAL_DRIVE_RATIO) / 60.0
                        ) * dt
                last_valid_time = now
            else:
                # A missing/invalid frame means the previous RPM is stale. Do
                # not continue integrating distance from an old value.
                last_valid_time = None

            time.sleep(0.01)
        except Exception:
            last_valid_time = None
            time.sleep(1)


def hr_data_handler(state):
    def callback(sender, data):
        if not data:
            return

        flags = data[0]
        if flags & 0x01:
            if len(data) < 3:
                return
            heart_rate = int.from_bytes(data[1:3], "little")
        else:
            if len(data) < 2:
                return
            heart_rate = data[1]

        state.heart_rate = heart_rate

    return callback


async def run_heart_rate_ble(state):
    while True:
        try:
            discovered = await BleakScanner.discover(timeout=5.0, return_adv=True)
            target = None

            for device, advertisement in discovered.values():
                uuids = {uuid.lower() for uuid in advertisement.service_uuids}
                if HEART_RATE_UUID.lower() in uuids:
                    target = device
                    break

            if not target and HEART_RATE_NAME_HINTS:
                for device, advertisement in discovered.values():
                    name = (advertisement.local_name or device.name or "").lower()
                    if any(hint in name for hint in HEART_RATE_NAME_HINTS):
                        target = device
                        break

            if target:
                async with BleakClient(target) as client:
                    if client.is_connected:
                        await client.start_notify(
                            HEART_RATE_UUID, hr_data_handler(state)
                        )
                        while client.is_connected:
                            await asyncio.sleep(1)
            else:
                await asyncio.sleep(5)
        except Exception:
            await asyncio.sleep(5)


def heart_rate_worker(state):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_heart_rate_ble(state))


class MinimalistDashboard:
    def __init__(self, state, hardware_connected=True):
        self.state = state
        self.hardware_connected = hardware_connected
        self.root = tk.Tk()
        self.root.title("PedalByte")
        self.root.geometry("750x920")
        self.root.configure(bg="#111111")
        self.root.attributes("-topmost", True)
        self.is_running = False
        self.session_start_time = None
        self.session_start_wheel_revs = 0.0
        self.caffeinate_process = None
        self.total_seconds_in_zone2 = 0
        self.total_active_seconds = 0
        self.accumulated_calories = 0.0
        self.current_streak_seconds = 0
        self.has_hit_zone2_yet = False
        self.reference_speed_mps = 0.0
        self.in_recovery_mode = False
        self.recovery_start_time = None
        self.recovery_initial_hr = 0
        self.hr_samples = []
        self.hr_history = [100] * 40
        self.metrics = {}
        self.build_ui()
        self.update_loop()

    def build_ui(self):
        tk.Label(self.root, text="PEDALBYTE", fg="#ffffff", bg="#111111", font=("Helvetica Neue", 22, "bold")).pack(pady=(24, 4))
        status = "ADAPTER CONNECTED" if self.hardware_connected else "WAITING FOR BIKE ADAPTER"
        tk.Label(self.root, text=status, fg="#888888", bg="#111111", font=("Helvetica Neue", 9, "bold")).pack()
        self.lbl_timer = tk.Label(self.root, text="00:00:00", fg="#ffffff", bg="#111111", font=("Helvetica Neue", 30, "bold")); self.lbl_timer.pack(pady=18)
        self.lbl_hr = tk.Label(self.root, text="-- BPM", fg="#ffffff", bg="#111111", font=("Helvetica Neue", 20, "bold")); self.lbl_hr.pack()
        grid=tk.Frame(self.root,bg="#111111"); grid.pack(pady=20)
        for i,(label,val) in enumerate([("RPM","0.0"),("WATTS","0 W"),("SPEED","0.0 mph"),("DISTANCE","0.00 mi"),("LEVEL","0"),("CALORIES","0 kcal"),("FAT BURNED","0.0 g"),("EFFICIENCY","READY")]):
            cell=tk.Frame(grid,bg="#191919",width=160,height=85); cell.grid(row=i//2,column=i%2,padx=6,pady=6); cell.grid_propagate(False)
            tk.Label(cell,text=label,fg="#777777",bg="#191919",font=("Helvetica Neue",8,"bold")).pack(pady=(12,2))
            lab=tk.Label(cell,text=val,fg="#ffffff",bg="#191919",font=("Helvetica Neue",15,"bold")); lab.pack()
            self.metrics[label]=lab
        self.lbl_coach=tk.Label(self.root,text="PRESS START TO RECORD ACTIVE SESSION",fg="#ffffff",bg="#1E1E1E",wraplength=640,font=("Helvetica Neue",10,"bold"),padx=14,pady=14); self.lbl_coach.pack(fill="x",padx=40,pady=18)
        controls=tk.Frame(self.root,bg="#111111"); controls.pack()
        tk.Button(controls,text="START",command=self.start_session,width=12).grid(row=0,column=0,padx=6)
        tk.Button(controls,text="STOP",command=self.stop_session,width=12).grid(row=0,column=1,padx=6)

    def session_distance_miles(self):
        delta_revs = max(0.0, self.state.total_wheel_revs - self.session_start_wheel_revs)
        return (delta_revs * VIRTUAL_WHEEL_CIRCUMFERENCE_M) / 1609.344

    def start_session(self):
        self.is_running=True; self.session_start_time=time.time(); self.session_start_wheel_revs=self.state.total_wheel_revs; self.total_active_seconds=0; self.total_seconds_in_zone2=0; self.accumulated_calories=0; self.current_streak_seconds=0; self.has_hit_zone2_yet=False; self.hr_samples=[]
        self.lbl_coach.config(text="SESSION ACTIVE — PEDALBYTE IS RECORDING YOUR RIDE")

    def stop_session(self):
        if not self.is_running or self.session_start_time is None: return
        elapsed=int(time.time()-self.session_start_time)
        self.is_running=False
        self.write_workout(elapsed)
        self.lbl_coach.config(text="SESSION SAVED — READY FOR ANOTHER RIDE")
        self.session_start_time=None

    def write_workout(self, elapsed):
        path="workout_history.csv"; exists=os.path.exists(path)
        duration=f"{elapsed//3600}:{(elapsed%3600)//60:02d}:{elapsed%60:02d}"
        dist=self.session_distance_miles()
        avg_hr=int(sum(self.hr_samples)/len(self.hr_samples)) if self.hr_samples else 0
        with open(path,"a",newline="") as f:
            w=csv.writer(f)
            if not exists: w.writerow(["Timestamp","Duration","Avg HR","Distance (mi)","Calories","Fat Burned","Zone 2 Efficiency"])
            eff=(self.total_seconds_in_zone2/self.total_active_seconds*100) if self.total_active_seconds else 0
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"),duration,avg_hr,f"{dist:.2f}",f"{self.accumulated_calories:.0f}",f"{(self.accumulated_calories*0.65/9):.1f}",f"{eff:.0f}%"])

    def update_loop(self):
        hr,rpm,watts=self.state.heart_rate,self.state.rpm,self.state.watts
        distance=self.session_distance_miles() if self.is_running else 0.0
        speed=calculate_speed_mph(rpm)
        if self.is_running and self.session_start_time is not None:
            elapsed=int(time.time()-self.session_start_time); h,rem=divmod(elapsed,3600); m,s=divmod(rem,60); self.lbl_timer.config(text=f"{h:02d}:{m:02d}:{s:02d}")
            if rpm>5:
                self.total_active_seconds += .25
                if hr>0: self.hr_samples.append(hr)
                self.accumulated_calories += max(0.0,(((0.4472*hr)-(0.1263*USER_WEIGHT_KG)+(0.074*USER_AGE)-20.4022)/(4.184*60.0))*0.25) if hr>90 else max(0.0,(watts*0.25)/(1000*0.24))
                if ZONE_LOW_BPM<=hr<=ZONE_HIGH_BPM: self.total_seconds_in_zone2+=.25; self.current_streak_seconds+=.25
                else: self.current_streak_seconds=0
                self.has_hit_zone2_yet = self.has_hit_zone2_yet or (ZONE_LOW_BPM<=hr<=ZONE_HIGH_BPM)
        else: self.lbl_timer.config(text="00:00:00")
        self.lbl_hr.config(text=f"{hr} BPM" if hr>0 else "-- BPM")
        self.metrics["RPM"].config(text=f"{rpm:.1f}"); self.metrics["WATTS"].config(text=f"{watts:.0f} W"); self.metrics["SPEED"].config(text=f"{speed:.1f} mph"); self.metrics["DISTANCE"].config(text=f"{distance:.2f} mi"); self.metrics["LEVEL"].config(text=str(self.state.level)); self.metrics["CALORIES"].config(text=f"{self.accumulated_calories:.0f} kcal"); self.metrics["FAT BURNED"].config(text=f"{self.accumulated_calories*0.65/9:.1f} g")
        efficiency=(self.total_seconds_in_zone2/self.total_active_seconds*100) if self.total_active_seconds else 0
        self.metrics["EFFICIENCY"].config(text=f"{efficiency:.0f}%" if self.total_active_seconds else "READY")
        self.root.after(250,self.update_loop)

    def start(self):
        self.root.mainloop()


def main():
    state=BikeState(); reader=None; hardware_connected=False
    try:
        if SERIAL_PORT: reader=SerialReader(SERIAL_PORT,BAUDRATE,state); hardware_connected=True
    except Exception as exc: print(f"[WARNING] Hardware link failed: {exc}")
    if hardware_connected: threading.Thread(target=serial_worker,args=(reader,state),daemon=True).start()
    threading.Thread(target=heart_rate_worker,args=(state,),daemon=True).start()
    threading.Thread(target=start_ble_server,args=(state,),daemon=True).start()
    MinimalistDashboard(state,hardware_connected).start()


if __name__ == "__main__":
    main()
