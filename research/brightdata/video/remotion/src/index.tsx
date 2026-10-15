import React from "react";
import { Composition, registerRoot, Freeze, useCurrentFrame } from "remotion";
import { Film } from "./Film";
import timeline from "./timeline.json";
import reel from "./reel.json";
const Reel = () => (
  <Freeze frame={reel.frames[useCurrentFrame()] ?? reel.frames.at(-1)!}>
    <Film withAudio={false} />
  </Freeze>
);
const Root = () => (
  <>
    <Composition
      id="BrightDataReel"
      component={Reel}
      width={1920}
      height={1080}
      fps={30}
      durationInFrames={timeline.durationInFrames}
    />
    <Composition
      id="BrightData"
      component={Film}
      width={1920}
      height={1080}
      fps={30}
      durationInFrames={timeline.durationInFrames}
    />
  </>
);
registerRoot(Root);
