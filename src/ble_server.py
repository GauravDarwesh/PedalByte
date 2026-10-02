import math
import struct
import time

import objc
from CoreBluetooth import (
    CBAdvertisementDataLocalNameKey,
    CBAdvertisementDataServiceUUIDsKey,
    CBAttributePermissionsReadable,
    CBCharacteristicPropertyNotify,
    CBCharacteristicPropertyRead,
    CBMutableCharacteristic,
    CBMutableService,
    CBPeripheralManager,
    CBPeripheralManagerStatePoweredOn,
    CBUUID,
)
from Foundation import NSData, NSObject
from PyObjCTools import AppHelper

from src.config import CADENCE_DEVICE_NAME, POWER_DEVICE_NAME, VIRTUAL_DRIVE_RATIO

UUID_CSC_SERVICE = CBUUID.UUIDWithString_("1816")
UUID_CSC_MEASUREMENT = CBUUID.UUIDWithString_("2A5B")
UUID_SENSOR_LOCATION = CBUUID.UUIDWithString_("2A5D")

UUID_CP_SERVICE = CBUUID.UUIDWithString_("1818")
UUID_CP_MEASUREMENT = CBUUID.UUIDWithString_("2A63")
UUID_CP_FEATURE = CBUUID.UUIDWithString_("2A65")


class DualChannelEngine:
    def __init__(self):
        self.last_update_time = time.time()
        self.total_crank_revs = 0
        self.fractional_crank_revs = 0.0
        self.crank_ticks = 0
        self.total_wheel_revs = 0
        self.fractional_wheel_revs = 0.0
        self.wheel_ticks = 0
        self.virtual_gear_ratio = VIRTUAL_DRIVE_RATIO


engine = DualChannelEngine()


def pack_csc_payload(rpm):
    now = time.time()
    dt = now - engine.last_update_time
    engine.last_update_time = now

    if rpm > 0:
        crank_revs_per_second = rpm / 60.0
        engine.fractional_crank_revs += crank_revs_per_second * dt
        whole_cranks = int(engine.fractional_crank_revs)
        if whole_cranks >= 1:
            engine.total_crank_revs = (engine.total_crank_revs + whole_cranks) % 65536
            engine.fractional_crank_revs -= whole_cranks
            time_per_rev_ms = (60.0 / rpm) * 1000.0
            engine.crank_ticks = int(
                engine.crank_ticks + (time_per_rev_ms * 1.024 * whole_cranks)
            ) % 65536

        wheel_revs_per_second = crank_revs_per_second * engine.virtual_gear_ratio
        engine.fractional_wheel_revs += wheel_revs_per_second * dt
        whole_wheels = int(engine.fractional_wheel_revs)
        if whole_wheels >= 1:
            engine.total_wheel_revs = (engine.total_wheel_revs + whole_wheels) % 4294967296
            engine.fractional_wheel_revs -= whole_wheels
            time_per_wheel_ms = (
                60.0 / (rpm * engine.virtual_gear_ratio)
            ) * 1000.0
            engine.wheel_ticks = int(
                engine.wheel_ticks + (time_per_wheel_ms * 1.024 * whole_wheels)
            ) % 65536

    flags = 0x03
    return struct.pack(
        "<BIHHH",
        flags,
        engine.total_wheel_revs,
        engine.wheel_ticks,
        engine.total_crank_revs,
        engine.crank_ticks,
    )


def pack_power_payload(watts):
    if not math.isfinite(watts):
        watts = 0.0
    watts_int = max(-32768, min(32767, int(round(watts))))
    return struct.pack("<Hh", 0, watts_int)


class CadencePeripheralDelegate(NSObject):
    def initWithBikeState_(self, bike_state):
        self = objc.super(CadencePeripheralDelegate, self).init()
        self.bike_state = bike_state
        self.manager = None
        self.char = None
        return self

    def start(self):
        self.manager = CBPeripheralManager.alloc().initWithDelegate_queue_(self, None)

    def peripheralManagerDidUpdateState_(self, peripheral):
        if peripheral.state() == CBPeripheralManagerStatePoweredOn:
            self.char = CBMutableCharacteristic.alloc().initWithType_properties_value_permissions_(
                UUID_CSC_MEASUREMENT,
                CBCharacteristicPropertyNotify,
                None,
                0,
            )
            loc = CBMutableCharacteristic.alloc().initWithType_properties_value_permissions_(
                UUID_SENSOR_LOCATION,
                CBCharacteristicPropertyRead,
                NSData.dataWithBytes_length_(b"\x02", 1),
                CBAttributePermissionsReadable,
            )
            service = CBMutableService.alloc().initWithType_primary_(UUID_CSC_SERVICE, True)
            service.setCharacteristics_([self.char, loc])
            self.manager.addService_(service)

    def peripheralManager_didAddService_error_(self, peripheral, service, error):
        if error is not None:
            print(f"[BLE] Failed to add CSC service: {error}")
            return
        adv = {
            CBAdvertisementDataLocalNameKey: CADENCE_DEVICE_NAME,
            CBAdvertisementDataServiceUUIDsKey: [UUID_CSC_SERVICE],
        }
        self.manager.startAdvertising_(adv)


    def update(self):
        if self.manager and self.manager.isAdvertising() and self.char:
            data = pack_csc_payload(self.bike_state.rpm)
            ns_data = NSData.dataWithBytes_length_(data, len(data))
            self.manager.updateValue_forCharacteristic_onSubscribedCentrals_(
                ns_data, self.char, None
            )


class PowerPeripheralDelegate(NSObject):
    def initWithBikeState_(self, bike_state):
        self = objc.super(PowerPeripheralDelegate, self).init()
        self.bike_state = bike_state
        self.manager = None
        self.char = None
        return self

    def start(self):
        self.manager = CBPeripheralManager.alloc().initWithDelegate_queue_(self, None)

    def peripheralManagerDidUpdateState_(self, peripheral):
        if peripheral.state() == CBPeripheralManagerStatePoweredOn:
            self.char = CBMutableCharacteristic.alloc().initWithType_properties_value_permissions_(
                UUID_CP_MEASUREMENT,
                CBCharacteristicPropertyNotify,
                None,
                0,
            )
            feat = CBMutableCharacteristic.alloc().initWithType_properties_value_permissions_(
                UUID_CP_FEATURE,
                CBCharacteristicPropertyRead,
                NSData.dataWithBytes_length_(b"\x00\x00\x00\x00", 4),
                CBAttributePermissionsReadable,
            )
            service = CBMutableService.alloc().initWithType_primary_(UUID_CP_SERVICE, True)
            service.setCharacteristics_([self.char, feat])
            self.manager.addService_(service)

    def peripheralManager_didAddService_error_(self, peripheral, service, error):
        if error is not None:
            print(f"[BLE] Failed to add cycling power service: {error}")
            return
        adv = {
            CBAdvertisementDataLocalNameKey: POWER_DEVICE_NAME,
            CBAdvertisementDataServiceUUIDsKey: [UUID_CP_SERVICE],
        }
        self.manager.startAdvertising_(adv)

    def update(self):
        if self.manager and self.manager.isAdvertising() and self.char:
            data = pack_power_payload(self.bike_state.watts)
            ns_data = NSData.dataWithBytes_length_(data, len(data))
            self.manager.updateValue_forCharacteristic_onSubscribedCentrals_(
                ns_data, self.char, None
            )


class GlobalManager(NSObject):
    def initWithBikeState_(self, bike_state):
        self = objc.super(GlobalManager, self).init()
        self.bike_state = bike_state
        self.csc_dev = None
        self.cp_dev = None
        return self

    def run(self):
        print("[SYSTEM] Starting Bluetooth Cycling Speed/Cadence + Cycling Power peripherals...")
        self.csc_dev = CadencePeripheralDelegate.alloc().initWithBikeState_(self.bike_state)
        self.csc_dev.start()
        self.cp_dev = PowerPeripheralDelegate.alloc().initWithBikeState_(self.bike_state)
        self.cp_dev.start()
        self.loop()

    def loop(self):
        if self.csc_dev and self.cp_dev:
            self.csc_dev.update()
            self.cp_dev.update()
        AppHelper.callLater(0.25, self.loop)


def start_ble_server(bike_state):
    orchestrator = GlobalManager.alloc().initWithBikeState_(bike_state)
    orchestrator.run()
    try:
        AppHelper.runConsoleEventLoop()
    except KeyboardInterrupt:
        print("\n[BLE] Halting Server.")
