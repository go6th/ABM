import json, sys, openpyxl, re
man=json.load(open('/home/sakib/ABM Generator/docs/parity/reference_manifest.json'))
d=sys.argv[1]; prefix=sys.argv[2]
mapping={'Job_Postings.xlsx':f'{prefix}_Job_Postings.xlsx','People_List.xlsx':f'{prefix}_People_List.xlsx','Tech_Stack_and_Teams.xlsx':f'{prefix}_Tech_Stack_and_Teams.xlsx'}
print([k for k in man])
for ref,fn in mapping.items():
    wb=openpyxl.load_workbook(f'{d}/{fn}')
    print('=====',fn,wb.sheetnames, 'expected', [s['name'] for s in man[ref]])
    for s in man[ref]:
        nm=s['name']
        if nm not in wb.sheetnames:
            print('  MISSING sheet',nm); continue
        ws=wb[nm]
        hr=s['header_row']
        got=[c.value for c in ws[hr]]
        got=[g for g in got if g is not None]
        exp=[h.replace('{prefix}',prefix.replace('_',' ')) for h in s['headers']]
        ok = [str(g) for g in got[:len(exp)]]==exp
        print('  sheet',nm,'order',wb.sheetnames.index(nm),'exp',s['order'],'dims',ws.dimensions,'freeze',ws.freeze_panes,'exp',s['freeze'],'filter',ws.auto_filter.ref,'exp',s['filter'],'rows',ws.max_row)
        if not ok:
            print('   HEADER DIFF\n    got',got,'\n    exp',exp)
        else: print('   headers OK')
        mg=sorted(str(m) for m in ws.merged_cells.ranges)
        if mg!=sorted(s['merged']): print('   merged got',mg,'exp',sorted(s['merged']))
