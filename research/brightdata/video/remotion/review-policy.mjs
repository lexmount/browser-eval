// Expected visible emphasis. Aliases describe intentional scene-specific groups,
// not exemptions from checking whether a spoken referent exists on screen.
const neutralScenes = ['history','source','stack','matrix','unlocker','browser','crawl','scraper','serp','matrix-recap','customers','fornova','quality','ai','fields','reuse','recap','roadmap'];
const aliases = {
  route: {whole:['access','extraction','data','intelligence']},
  dependency: {
    whole:['network','geo','access','extract','browser','datasets','firehose','archive','entries','intelligence','managed','search','business','agents'],
    'data-path':['network','access','extract','datasets','firehose','archive','intelligence','managed','business'],
    'agent-path':['network','access','browser','entries','agents'],
    'entry-path':['access','extract','browser','entries','agents'],
    access:['network','access','extract','browser'],extraction:['extract','datasets','firehose','archive'],data:['datasets','archive','intelligence','search'],
  },
  browser:{session:['input'],actions:['service']},
  crawl:{limit:['boundary']},
  fornova:{scope:['scope','refresh']},
  quality:{availability:['coverage','success'],usability:['correctness','freshness'],operations:['maintenance','responsibility']},
  ai:{boundary:['direction']},
  lexmount:{whole:['execution','session','extraction','quality'],execution:['execution','session'],loop:['execution','session','extraction','quality','loop']},
  fields:{record:['fields','record']},
  recap:{intelligence:['intelligence','agent']},
  roadmap:{validation:['repeat','qualified','cost']},
};
export function expectedFocus(scene,target) {
  if(aliases[scene]?.[target]) return aliases[scene][target];
  if(target==='whole') {
    if(neutralScenes.includes(scene)) return [];
    throw Error(`Undeclared overview: ${scene}`);
  }
  return [target];
}
export function validateSample(expected,actual) {
  const failures=[];
  if(actual.scenes.length!==1 || actual.scenes[0]!==expected.scene) failures.push({code:'scene',expected:expected.scene,actual:actual.scenes});
  if(actual.clipped.length) failures.push({code:'overflow',elements:actual.clipped});
  const want=[...new Set(expectedFocus(expected.scene,expected.target))].sort();
  const got=[...new Set(actual.active)].sort();
  if(JSON.stringify(want)!==JSON.stringify(got)) failures.push({code:'focus',expected:want,actual:got});
  return failures;
}
