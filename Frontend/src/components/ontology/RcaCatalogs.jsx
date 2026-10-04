import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import VirtualTable from '../shared/VirtualTable'

export default function RcaCatalogs({ kpiCatalog, alarmCatalog }) {
  const [showKpi, setShowKpi] = useState(false)
  const [showAlarm, setShowAlarm] = useState(false)

  return (
    <section className="ontology-catalogs">
      <h3 className="ontology-section-title">RCA Catalogs</h3>
      <p className="ontology-catalog-intro">Grounded diagnostic vocabulary for RCA</p>

      <div className="catalog-section">
        <h4 className="catalog-toggle" onClick={() => setShowKpi(!showKpi)}>
          <ChevronDown size={16} style={{ transform: showKpi ? 'rotate(180deg)' : 'rotate(0deg)' }} />
          KPI Catalog ({kpiCatalog?.length || 0})
        </h4>
        {showKpi && kpiCatalog && (
          <div className="catalog-table">
            <VirtualTable
              rows={kpiCatalog}
              columns={['KPI Name', 'Domain', 'Unit', 'RCA Usage']}
              renderCellValue={(row, col) => {
                const val = row[col]
                return val === null ? '—' : String(val)
              }}
            />
          </div>
        )}
      </div>

      <div className="catalog-section">
        <h4 className="catalog-toggle" onClick={() => setShowAlarm(!showAlarm)}>
          <ChevronDown size={16} style={{ transform: showAlarm ? 'rotate(180deg)' : 'rotate(0deg)' }} />
          Alarm Probable Causes ({alarmCatalog?.length || 0})
        </h4>
        {showAlarm && alarmCatalog && (
          <div className="catalog-table">
            <VirtualTable
              rows={alarmCatalog}
              columns={['Probable Cause', 'Domain', 'Default Severity', 'Recommended First Check']}
              renderCellValue={(row, col) => {
                const val = row[col]
                return val === null ? '—' : String(val)
              }}
            />
          </div>
        )}
      </div>
    </section>
  )
}
