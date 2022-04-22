# pyFieldMaxII
Coherent Field Max powermeter control.
A python wrapper for the FieldMax2Lib.dll.

```
import fieldmax 
FMII = fieldmax.FieldMax(r'C:\Program Files (x86)\Coherent\FieldMaxII PC\Drivers\Win10\FieldMax2Lib\x64\FieldMax2Lib.dll')
FMII.openDriver()
print( FMII.get_SerialNumber() )
FMII.closeDriver()
```
