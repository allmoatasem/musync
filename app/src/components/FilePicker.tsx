import { openFile } from '../api'

interface FilePickerProps {
  label: string
  value: string
  onChange: (path: string) => void
  placeholder?: string
  extensions?: string[]
}

export default function FilePicker({ label, value, onChange, placeholder, extensions }: FilePickerProps) {
  const handleBrowse = async () => {
    try {
      const path = await openFile(extensions)
      if (path) onChange(path)
    } catch (e) {
      console.error('openFile error:', e)
    }
  }

  return (
    <div className="file-row">
      <span className="file-label">{label}</span>
      <div className="file-input-row">
        <div className={`file-path ${value ? '' : 'empty'}`}>
          {value || (placeholder ?? 'No file selected')}
        </div>
        <button className="btn btn-browse btn-sm" onClick={handleBrowse}>
          Browse
        </button>
      </div>
    </div>
  )
}
