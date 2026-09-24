from pathlib import Path
import ast, hashlib, json, re, sys, tomllib
root = Path(sys.argv[1]); out = Path(sys.argv[2]); inv=json.loads((out/'inventory.json').read_text())
report={'files':[], 'test_functions':{}, 'documents':{}, 'json_summaries':{}, 'errors':[]}
for item in inv:
    p=root/item['file']; raw=p.read_bytes(); text=raw.decode('utf-8-sig'); entry={'file':item['file'], 'unchanged':hashlib.sha256(raw).hexdigest()==item['sha256']}
    if p.suffix=='.py':
        try:
            tree=ast.parse(text, filename=item['file']); compile(tree, str(p), 'exec'); entry['syntax']='ok'
            entry['imports']=[ast.unparse(n) for n in tree.body if isinstance(n,(ast.Import,ast.ImportFrom))]
            entry['classes']=[{'name':n.name,'line':n.lineno,'methods':[x.name for x in n.body if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef))]} for n in tree.body if isinstance(n,ast.ClassDef)]
            entry['functions']=[{'name':n.name,'line':n.lineno,'end':n.end_lineno} for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))]
            if p.name.startswith('test_'):
                report['test_functions'][item['file']]=[{'name':n.name,'line':n.lineno,'assertions':[ast.unparse(x)[:220] for x in ast.walk(n) if isinstance(x,ast.Assert)]} for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name.startswith('test_')]
        except Exception as e: report['errors'].append({'file':item['file'],'error':str(e)})
    elif p.suffix=='.json':
        try:
            obj=json.loads(text); entry['json']='ok'
            if p.name.startswith('probe_'):
                report['json_summaries'][item['file']]={k:({'status':v.get('status'),'models':list(v.get('models',{})),'checks':{m:{n:c.get('status') for n,c in mr.get('checks',{}).items()} for m,mr in v.get('models',{}).items()}} if isinstance(v,dict) else v) for k,v in obj.items()}
            if p.name=='package-lock.json': entry['packages']=len(obj.get('packages',{})); entry['root']=obj.get('packages',{}).get('',{})
        except Exception as e: report['errors'].append({'file':item['file'],'error':str(e)})
    elif p.suffix=='.toml': tomllib.loads(text); entry['toml']='ok'
    if p.suffix=='.md':
        report['documents'][item['file']]={'headings':[l for l in text.splitlines() if l.startswith('#')], 'claims':[{'line':i,'text':l[:400]} for i,l in enumerate(text.splitlines(),1) if re.search(r'TODO|FIXME|\[ \]|\[x\]|2026|probe|race|SSRF|PASS|DONE|334|rollback|backup|restore|CAPTCHA|retry',l,re.I)]}
    entry['review_flags']=[{'line':i,'text':l[:240]} for i,l in enumerate(text.splitlines(),1) if p.name!='package-lock.json' and re.search(r'TODO|FIXME|NotImplementedError|except Exception|Number\(id\)|shell=True|eval\(|exec\(|is_owner|show_sources|allow_all|pass$',l)]
    report['files'].append(entry)
report['counts']={'files':len(inv),'python':sum(x['file'].endswith('.py') for x in inv),'all_unchanged':all(x['unchanged'] for x in report['files']),'test_modules':len(report['test_functions']), 'test_function_definitions':sum(len(x) for x in report['test_functions'].values())}
(out/'static_analysis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report['counts'],indent=2)); print('errors',report['errors']);print('probe summaries:',json.dumps(report['json_summaries'],ensure_ascii=False,indent=2))
