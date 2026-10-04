import XLSX from 'xlsx'
import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const PROJECT_ROOT = path.resolve(__dirname, '..')
const WORKBOOK_PATH = path.resolve(PROJECT_ROOT, '..', 'Telecom_Conceptual_Ontology v4.xlsx')
const OUT_DIR = path.resolve(PROJECT_ROOT, 'public', 'data', 'ontology')

function readSheet(wb, sheetName, headerRowIndex) {
  const ws = wb.Sheets[sheetName]
  if (!ws) throw new Error(`Sheet "${sheetName}" not found`)

  const rows = XLSX.utils.sheet_to_json(ws, { header: 1 })
  const headers = rows[headerRowIndex]

  if (!headers) throw new Error(`No header row at index ${headerRowIndex} in sheet "${sheetName}"`)

  const result = []
  for (let i = headerRowIndex + 1; i < rows.length; i++) {
    const row = rows[i]
    if (!row || row.every(cell => cell === undefined || cell === '')) break

    const obj = {}
    for (let j = 0; j < headers.length; j++) {
      const header = headers[j]
      const cell = row[j]
      obj[header] = (cell === undefined || cell === '') ? null : cell
    }
    result.push(obj)
  }
  return result
}

function ensureDir(dirPath) {
  if (!fs.existsSync(dirPath)) {
    fs.mkdirSync(dirPath, { recursive: true })
  }
}

async function main() {
  console.log(`Reading workbook from ${WORKBOOK_PATH}...`)
  const wb = XLSX.readFile(WORKBOOK_PATH)

  ensureDir(OUT_DIR)
  ensureDir(path.join(OUT_DIR, 'fields'))

  console.log('Parsing sheets...')

  // Sheets 1-6: header at row 4 (0-indexed)
  const topLevel = readSheet(wb, 'Top-Level Classes', 3)
  const classes = readSheet(wb, 'Level 2 - Subclasses', 3)
  const fields = readSheet(wb, 'Level 3 - Fields & Sub-Types', 3)
  const relationshipTypes = readSheet(wb, 'Relationship Types', 3)
  const relationshipMapping = readSheet(wb, 'Relationship Mapping', 3)
  const useCases = readSheet(wb, 'Use Case Segmentation', 3)

  // Sheets 7-10: header at row 1 (0-indexed)
  const kpiCatalog = readSheet(wb, 'RCA KPI Catalog', 0)
  const alarmCatalog = readSheet(wb, 'RCA Alarm Cause Catalog', 0)
  const owlRules = readSheet(wb, 'OWL Conversion Rules', 0)
  const owlDatatypes = readSheet(wb, 'OWL Datatype Mapping', 0)

  // Verify row counts
  const assertions = {
    topLevel: [5, topLevel.length],
    classes: [630, classes.length],
    fields: [8583, fields.length],
    relationshipTypes: [339, relationshipTypes.length],
    relationshipMapping: [603, relationshipMapping.length],
    useCases: [4, useCases.length],
    kpiCatalog: [19, kpiCatalog.length],
    alarmCatalog: [14, alarmCatalog.length],
    owlRules: [24, owlRules.length],
    owlDatatypes: [7, owlDatatypes.length],
  }

  for (const [name, [expected, actual]] of Object.entries(assertions)) {
    if (expected !== actual) {
      throw new Error(`${name}: expected ${expected} rows, got ${actual}`)
    }
  }

  console.log('Verified row counts: ✓')

  // Compute derived counts
  const rca = {
    classes: classes.filter(r => r['RCA'] === 'Yes').length,
    relationshipTypes: relationshipTypes.filter(r => r['RCA'] === 'Yes').length,
  }

  if (rca.classes !== 163) throw new Error(`RCA classes: expected 163, got ${rca.classes}`)
  if (rca.relationshipTypes !== 85) throw new Error(`RCA rel types: expected 85, got ${rca.relationshipTypes}`)

  // Extract domains
  const domains = new Map()
  classes.forEach(r => {
    const code = r['Domain Code']
    const name = r['Telecom Domain']
    if (code && name && !domains.has(code)) {
      domains.set(code, { code, name, classes: 0, rcaClasses: 0 })
    }
  })

  // Count classes per domain
  classes.forEach(r => {
    const code = r['Domain Code']
    if (domains.has(code)) {
      const domain = domains.get(code)
      domain.classes++
      if (r['RCA'] === 'Yes') domain.rcaClasses++
    }
  })

  const byDomain = Array.from(domains.values()).sort((a, b) => a.code.localeCompare(b.code))

  if (byDomain.length !== 24) {
    throw new Error(`Expected 24 domains, got ${byDomain.length}`)
  }

  // Extract distinct Primary Standards
  const standardCounts = new Map()
  classes.forEach(r => {
    const std = r['Primary Standard']
    if (std) {
      standardCounts.set(std, (standardCounts.get(std) || 0) + 1)
    }
  })

  const standards = Array.from(standardCounts.entries())
    .sort((a, b) => b[1] - a[1])
    .map(([value, count]) => ({ value, count }))

  // Build summary
  const summary = {
    version: '1.0.0',
    iri: 'urn:kpmg:telecom:ontology',
    topLevelClasses: topLevel.length,
    domains: byDomain.length,
    classes: classes.length,
    fields: fields.length,
    relationshipTypes: relationshipTypes.length,
    relationshipMappings: relationshipMapping.length,
    rca: {
      classes: rca.classes,
      relationshipTypes: rca.relationshipTypes,
    },
    byDomain,
    standards,
  }

  // Write files
  console.log('Writing JSON files...')
  fs.writeFileSync(path.join(OUT_DIR, 'summary.json'), JSON.stringify(summary, null, 2))
  fs.writeFileSync(path.join(OUT_DIR, 'top-level.json'), JSON.stringify(topLevel, null, 2))
  fs.writeFileSync(path.join(OUT_DIR, 'classes.json'), JSON.stringify(classes, null, 2))
  fs.writeFileSync(path.join(OUT_DIR, 'relationship-types.json'), JSON.stringify(relationshipTypes, null, 2))
  fs.writeFileSync(path.join(OUT_DIR, 'relationship-mapping.json'), JSON.stringify(relationshipMapping, null, 2))
  fs.writeFileSync(path.join(OUT_DIR, 'use-cases.json'), JSON.stringify(useCases, null, 2))
  fs.writeFileSync(path.join(OUT_DIR, 'kpi-catalog.json'), JSON.stringify(kpiCatalog, null, 2))
  fs.writeFileSync(path.join(OUT_DIR, 'alarm-catalog.json'), JSON.stringify(alarmCatalog, null, 2))
  fs.writeFileSync(path.join(OUT_DIR, 'owl-rules.json'), JSON.stringify(owlRules, null, 2))
  fs.writeFileSync(path.join(OUT_DIR, 'owl-datatypes.json'), JSON.stringify(owlDatatypes, null, 2))

  // Split fields by Top Domain
  const fieldsByDomain = new Map()
  fields.forEach(r => {
    const domain = r['Top Domain']
    if (domain) {
      if (!fieldsByDomain.has(domain)) {
        fieldsByDomain.set(domain, [])
      }
      fieldsByDomain.get(domain).push(r)
    }
  })

  fieldsByDomain.forEach((rows, domain) => {
    const filename = domain.toLowerCase() + '.json'
    fs.writeFileSync(path.join(OUT_DIR, 'fields', filename), JSON.stringify(rows, null, 2))
  })

  console.log(`✓ Built ontology JSON to ${OUT_DIR}`)
  console.log(`  - 10 primary files + 5 field-split files created`)
  console.log(`  - Verified: 5 top-level classes, 24 domains, 630 classes, 8,583 fields`)
  console.log(`  - RCA: 163 classes, 85 relationship types`)
}

main().catch(err => {
  console.error('Build failed:', err.message)
  process.exit(1)
})
