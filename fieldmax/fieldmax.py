import ctypes as c
import os, time, sys

class FieldMax(object):

    def __init__(self, dllPath=r'C:\Program Files (x86)\Coherent\FieldMaxII PC\Drivers\Win10\FieldMax2Lib\x64\FieldMax2Lib.dll'):
        """
        Connect to Library
        """
        if not os.path.exists(dllPath):
            print("ERROR: DLL not found")
        self.o = c.windll.LoadLibrary(dllPath)
        #self.o = c.CDLL(dllPath)

    def openDriver(self):
        dll_conn = self.o.fm2LibOpenDriver
        dll_conn.restype = c.c_int32
        dll_conn.argtypes = [c.c_int16]
        return dll_conn(c.c_int16(0))
    
    def closeDriver(self):
        dll_conn = self.o.fm2LibCloseDriver
        dll_conn.restype = c.c_int16
        dll_conn.argtypes = [c.c_int32]
        return dll_conn(c.c_int32(0)) 

    def deInit(self):
        dll_conn = self.o.fm2LibDeInit
        dll_conn.restype = c.c_int16
        dll_conn.argtypes = []
        return dll_conn() 

    def _bytes2values(self, l):
        float_p = c.cast(l, c.POINTER(c.c_float))
        power = [float_p[0],float_p[2],float_p[4],float_p[6],float_p[8],float_p[10],float_p[12],float_p[14]]
        period = [float_p[1],float_p[3],float_p[5],float_p[7],float_p[9],float_p[11],float_p[13],float_p[15]]
        return (power, period)
    
    def _sendReply(self, s):
        dll_conn = self.o.fm2LibPackagedSendReply
        dll_conn.restype = c.c_int16
        dll_conn.argtypes = [c.c_int32, c.c_char_p, c.POINTER((c.c_char*100)), c.POINTER(c.c_int16)]
        commandBuffer = c.c_char_p(s)
        returnBuffer = (c.c_char*100)()
        ret = dll_conn(c.c_int32(0), commandBuffer, c.pointer(returnBuffer), c.pointer(c.c_int16(100)))
        return returnBuffer.value.decode()

    def sync(self):
        dll_conn = self.o.fm2LibSync
        dll_conn.restype = c.c_int16
        dll_conn.argtypes = [c.c_int32]
        return dll_conn(0)
        
    def get_SerialNumber(self):
        dll_conn = self.o.fm2LibGetSerialNumber
        dll_conn.restype = c.c_int16
        dll_conn.argtypes = [c.c_int32, c.POINTER((c.c_char*16)), c.POINTER(c.c_int16)]
        returnBuffer = (c.c_char*16)()
        ret = dll_conn(c.c_int32(0), returnBuffer, c.pointer(c.c_int16(16)))
        return returnBuffer.value.decode()

    def get_dataArray(self):
        dll_conn = self.o.fm2LibGetData
        dll_conn.restype = c.c_int16
        #dll_conn.argtypes=[c.c_int32,c.c_uint8,c.c_int16]
        value_array = (c.c_uint8*64)()
        addr = c.c_int16(8)
        ret = dll_conn(0,c.pointer(value_array),c.pointer(addr))     
        return self._bytes2values(value_array)[0]
    
    def get_dataPoint(self):
        dll_conn = self.o.fm2LibGetData
        dll_conn.restype = c.c_int16
        #dll_conn.argtypes=[c.c_int32,c.c_uint8,c.c_int16]
        value_array = (c.c_uint8*64)()
        addr = c.c_int16(8)
        self.sync()
        ret = dll_conn(0,c.pointer(value_array),c.pointer(addr))     
        return self._bytes2values(value_array)[0][0]

    def _getStatusChangeList(self):
        dll_conn = self.o.fm2LibGetStatusChangeList
        dll_conn.restype = c.c_int16
        dll_conn.argtypes = [c.POINTER(c.c_uint16*3)]
        returnBuffer = (c.c_uint16*3)()
        ret = dll_conn(c.pointer(returnBuffer))
        return list(returnBuffer)
    
    def _zeroStart(self):
        dll_conn = self.o.fm2LibZeroStart
        dll_conn.restype = c.c_int16
        dll_conn.argtypes = [c.c_int32]
        return dll_conn(c.c_int32(0))

    def _zeroReply(self):
        dll_conn = self.o.fm2LibGetZeroReply
        dll_conn.restype = c.c_int16
        dll_conn.argtypes = [c.c_int32]
        return dll_conn(c.c_int32(0))

    def zeroing(self):
        self._zeroStart()
        ans = self._zeroReply()
        while ans == 1:
            ans = self._zeroReply()
 
if __name__ == "__main__":
    import fieldmax
    
    FMII = fieldmax.FieldMax(r'C:\Program Files (x86)\Coherent\FieldMaxII PC\Drivers\Win10\FieldMax2Lib\x64\FieldMax2Lib.dll')
    FMII.openDriver()
    print( FMII.get_SerialNumber() )
    FMII.sync()
    print( FMII.get_dataPoint() )
    FMII.zeroing()
    print( FMII.get_dataPoint() )
    FMII.closeDriver()
