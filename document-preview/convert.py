"""UNO client: load the supplied file read-only, without macros/link updates."""
import sys
import time
from pathlib import Path
import uno
from com.sun.star.beans import PropertyValue


def props(**values):
    return tuple(PropertyValue(Name=name, Value=value) for name, value in values.items())


context = uno.getComponentContext()
resolver = context.ServiceManager.createInstanceWithContext('com.sun.star.bridge.UnoUrlResolver', context)
deadline = time.monotonic() + 12
while True:
    try:
        remote = resolver.resolve(f'uno:pipe,name={sys.argv[1]};urp;StarOffice.ComponentContext')
        break
    except Exception:
        if time.monotonic() > deadline:
            raise RuntimeError('Office did not start') from None
        time.sleep(0.1)
desktop = remote.ServiceManager.createInstanceWithContext('com.sun.star.frame.Desktop', remote)
clean = len(sys.argv) > 5 and sys.argv[5] == 'clean'
#: Материалы карт: таблица Calc или презентация Impress, только PDF для просмотра.
office = len(sys.argv) > 5 and sys.argv[5] == 'office'
# The clean view edits only this in-memory copy; the source file stays
# read-only on disk and nothing is stored back to it.
document = desktop.loadComponentFromURL(Path(sys.argv[2]).as_uri(), '_blank', 0,
    props(Hidden=True, ReadOnly=not clean, UpdateDocMode=0, MacroExecutionMode=0))
if document is None:
    raise RuntimeError('Not a readable document')
if office:
    if document.supportsService('com.sun.star.sheet.SpreadsheetDocument'):
        # Лист целиком на одной странице: широкий регламент не режется на куски.
        office_filter = ('calc_pdf_Export', props(SinglePageSheets=True, ExportNotes=False))
    elif document.supportsService('com.sun.star.presentation.PresentationDocument'):
        office_filter = ('impress_pdf_Export', props(ExportNotes=False, ExportNotesPages=False))
    else:
        document.close(True)
        raise RuntimeError('Not a spreadsheet or presentation')
elif not document.supportsService('com.sun.star.text.TextDocument'):
    raise RuntimeError('Not a readable Word document')


def clear_marking(text):
    cursor = text.createTextCursor()
    cursor.gotoStart(False)
    cursor.gotoEnd(True)
    for name in ('CharHighlight', 'CharBackColor'):
        try:
            cursor.setPropertyValue(name, -1)
        except Exception:
            pass


def text_in_force(document):
    """Proposed Word edits are not yet the document's text: reject them for
    reading, and drop highlighter marks that only flag passages for review."""
    helper = remote.ServiceManager.createInstanceWithContext('com.sun.star.frame.DispatchHelper', remote)
    helper.executeDispatch(document.getCurrentController().getFrame(), '.uno:RejectAllTrackedChanges', '', 0, ())
    if document.getRedlines().createEnumeration().hasMoreElements():
        raise RuntimeError('Tracked changes remain')
    clear_marking(document.getText())
    tables = document.getTextTables()
    for index in range(tables.getCount()):
        table = tables.getByIndex(index)
        for name in table.getCellNames():
            clear_marking(table.getCellByName(name))


try:
    if clean:
        text_in_force(document)
    if office:
        document.storeToURL(Path(sys.argv[3]).as_uri(), props(FilterName=office_filter[0], Overwrite=True,
            FilterData=office_filter[1]))
    elif len(sys.argv) > 4 and sys.argv[4] == 'docx':
        # A transient navigation source; do not accept/reject tracked changes.
        document.storeToURL(Path(sys.argv[3]).as_uri(), props(FilterName='Office Open XML Text', Overwrite=True))
    else:
        document.storeToURL(Path(sys.argv[3]).as_uri(), props(FilterName='writer_pdf_Export', Overwrite=True,
            FilterData=props(ExportBookmarks=True, ExportNotes=False, IsAddStream=False)))
finally:
    document.close(True)
