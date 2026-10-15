// Explicit CI-only compilation fixture. Never substitute it for a real movie.
import fs from 'node:fs';
if(fs.existsSync('src/timeline.json'))throw Error('Refusing to overwrite an existing film timeline');
const fixture={fps:30,duration:6,durationInFrames:180,scenes:[{id:'example',chapter:0,title:'Synthetic scene',start:0,end:6,speechStart:1,speechEnd:4,lead:1,hold:2,from:0,durationInFrames:180,recap:'Review the same object'}],events:[{scene:'example',target:'whole',text:'Example',start:1,end:4}],captions:[{scene:'example',text:'Synthetic narration',start:1,end:4}],chapters:[{title:'Example',start:0}]};
fs.writeFileSync('src/timeline.json',JSON.stringify(fixture));
fs.writeFileSync('src/reel.json',JSON.stringify({frames:[0],segments:[]}));
