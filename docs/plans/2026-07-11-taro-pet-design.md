# Taro Pet Design

## Intent

Create a Codex-compatible v2 animated pet closely resembling the person in `F:\cool\jiangning\VQQY5110.JPG`. The pet should feel joyful, grounded, and quietly capable, with a soft 3D-toy finish that remains readable at the app's small display size.

## Character

- **Name:** Taro
- **Form:** chibi human portrait pet
- **Identity anchors:** oversized round black glasses, tied-back dark brown hair with loose side strands, broad joyful smile, warm complexion, and the black-and-stone color-block outdoor jacket from the reference photo
- **Proportions:** compact whole body, slightly oversized head, short sturdy limbs, small dark boots
- **Personality:** warm, persistent, practical, calm under pressure, and delighted when work succeeds
- **Props:** none; personality and task states must read through pose and expression alone

## Visual Direction

Use a polished soft 3D collectible-toy style with gently rounded forms, matte fabric, subtle molded hair, clean glasses, and large but recognizable facial features. Preserve the photo's natural earth palette. Avoid glossy plastic, photoreal skin, anime exaggeration, text, logos, scenery, detached effects, and tiny technical details.

The likeness should survive reduction to a `192x208` cell. Prioritize the silhouette, glasses, smile, hair shape, jacket color blocking, and warm expression over fine facial detail.

## Animation Language

Taro's nine standard animation rows should express:

- quiet breathing and blinking while idle
- clear alternating directional travel without effects
- an open, friendly hand wave
- a compact buoyant jump
- a gentle disappointed slump for failure
- an attentive, expectant asking pose while waiting
- focused active work through posture and gaze rather than literal running
- careful review through a forward lean, eye focus, and restrained head movement

The sixteen look directions should use eyes first, then subtle eyelid, eyebrow, head, neck, and upper-torso follow-through. The lower body and feet remain anchored. Glasses stay rigidly attached to the face; hair strands and jacket collar may lag slightly. No whole-sprite rotation or facial warping.

## Output Contract

Produce and install a Codex v2 pet containing an `8x11` atlas of `192x208` cells, yielding a final `1536x2288` spritesheet and `pet.json` with `spriteVersionNumber: 2`. Complete deterministic extraction, chroma cleanup, atlas validation, contact-sheet review, animation previews, cardinal-direction approval, three isolated blind direction reviews, per-direction semantic review, and continuity review before packaging.

## Acceptance

- Taro is immediately recognizable as the person in the reference photo.
- Identity, face, glasses, hair, jacket, palette, proportions, and material remain consistent across all rows.
- Every state reads correctly without text, scenery, shadows, guide marks, or detached effects.
- All four cardinal look directions are unmistakable at normal pet size.
- The final atlas passes the hatch-pet v2 deterministic and visual QA requirements.
