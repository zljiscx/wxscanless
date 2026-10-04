from enum import IntFlag

import comtypes.gen._00020430_0000_0000_C000_000000000046_0_2_0 as __wrapper_module__
from comtypes.gen._00020430_0000_0000_C000_000000000046_0_2_0 import (
    IFontDisp, IFont, OLE_XSIZE_PIXELS, IUnknown, Checked, Picture,
    Library, CoClass, Color, FONTSTRIKETHROUGH, _lcid, IDispatch,
    OLE_YPOS_CONTAINER, FONTSIZE, OLE_YSIZE_CONTAINER, Unchecked,
    IFontEventsDisp, Monochrome, FONTBOLD, StdPicture,
    OLE_OPTEXCLUSIVE, VARIANT_BOOL, OLE_XSIZE_HIMETRIC, IEnumVARIANT,
    OLE_ENABLEDEFAULTBOOL, FontEvents, FONTUNDERSCORE,
    OLE_XPOS_PIXELS, GUID, OLE_XPOS_CONTAINER, Font, DISPPARAMS,
    dispid, Gray, typelib_path, DISPMETHOD, OLE_YSIZE_HIMETRIC,
    FONTNAME, OLE_COLOR, OLE_XPOS_HIMETRIC, OLE_YPOS_HIMETRIC,
    StdFont, _check_version, EXCEPINFO, OLE_YSIZE_PIXELS,
    OLE_YPOS_PIXELS, OLE_HANDLE, BSTR, IPicture, IPictureDisp,
    OLE_CANCELBOOL, Default, FONTITALIC, VgaColor, COMMETHOD,
    OLE_XSIZE_CONTAINER, DISPPROPERTY, HRESULT
)


class LoadPictureConstants(IntFlag):
    Default = 0
    Monochrome = 1
    VgaColor = 2
    Color = 4


class OLE_TRISTATE(IntFlag):
    Unchecked = 0
    Checked = 1
    Gray = 2


__all__ = [
    'IFontDisp', 'IFont', 'Gray', 'OLE_XSIZE_PIXELS', 'typelib_path',
    'Checked', 'OLE_TRISTATE', 'OLE_YSIZE_HIMETRIC', 'Picture',
    'Library', 'FONTNAME', 'OLE_COLOR', 'Color', 'FONTSTRIKETHROUGH',
    'OLE_XPOS_HIMETRIC', 'OLE_YPOS_HIMETRIC', 'StdFont',
    'OLE_YPOS_CONTAINER', 'FONTSIZE', 'OLE_YSIZE_PIXELS',
    'OLE_YSIZE_CONTAINER', 'OLE_YPOS_PIXELS', 'LoadPictureConstants',
    'Unchecked', 'OLE_HANDLE', 'IFontEventsDisp', 'Monochrome',
    'FONTBOLD', 'IPicture', 'IPictureDisp', 'OLE_CANCELBOOL',
    'StdPicture', 'Default', 'FONTITALIC', 'OLE_OPTEXCLUSIVE',
    'OLE_XSIZE_HIMETRIC', 'VgaColor', 'OLE_ENABLEDEFAULTBOOL',
    'FontEvents', 'FONTUNDERSCORE', 'OLE_XPOS_PIXELS',
    'OLE_XPOS_CONTAINER', 'OLE_XSIZE_CONTAINER', 'Font'
]

