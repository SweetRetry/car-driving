import bpy, bmesh, math, os, random
import numpy as np
from mathutils import Vector, Matrix, Euler

FPS, FRAMES = 24, 288
LANE = 3.7
ROAD_END = 1500
STEP = 2.0
TURN_START, TURN_END, TURN = 40, 280, math.radians(24)
SUN_AZIMUTH, SUN_ELEVATION = math.radians(17), math.radians(5)
TOWARD_SUN = Vector((math.sin(SUN_AZIMUTH) * math.cos(SUN_ELEVATION), math.cos(SUN_AZIMUTH) * math.cos(SUN_ELEVATION), math.sin(SUN_ELEVATION)))
MEDIAN = -1.8
RIGHT_EDGE = LANE * 3
GUARDRAIL = RIGHT_EDGE + 2.9
OPPOSING_EDGE = -3.6
GANTRY_S, OVERPASS_S = 345, 540
SUN_COLOR = (1.0, 0.52, 0.2)
SUN_ENERGY, SUN_DISC, SUN_HALO = 14, 120.0, 2.0
SKY_HORIZON, SKY_ZENITH = (1.3, 0.62, 0.26), (0.55, 0.34, 0.22)
HAZE_DENSITY, HAZE_SCALE_HEIGHT = 0.0011, 14.0
HAZE_COLOR = (1.0, 0.72, 0.42)
BLOOM_STRENGTH = 0.18
TAILLIGHT_GLOW = 20.0
GEAR_SHIFT_FRAME = 160
HUD_CENTER, HUD_SCALE = (0.05, 7.7, 1.54), 1.45
BOKEH_GLOW, CAMERA_SHAKE = 0.45, 0.0006
EXPOSURE, CLOUD_GAIN = -0.3, 0.6
LENS_DISPERSION = 0.012
FONT_PATH = "/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf"
MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
MODELS = {
    "Coupe": ("coupe.glb", 4.95),
    "SUV": ("suv.glb", 4.7),
    "Sedan": ("sedan.glb", 4.75),
}


def heading(s):
    u = min(max((s - TURN_START) / (TURN_END - TURN_START), 0), 1)
    return TURN * u * u * (3 - 2 * u)


def _centerline():
    points, p = [], Vector((0, 0, 0))
    for i in range(ROAD_END + 2):
        points.append(p.copy())
        h = heading(i + 0.5)
        p += Vector((math.sin(h), math.cos(h), 0))
    return points


CENTER = _centerline()


def road_point(s, lateral=0.0, z=0.0):
    s = min(max(s, 0), ROAD_END)
    i = int(s)
    c = CENTER[i].lerp(CENTER[i + 1], s - i)
    h = heading(s)
    return c + Vector((math.cos(h), -math.sin(h), 0)) * lateral + Vector((0, 0, z))


def lane_center(i):
    return LANE * (i + 0.5)


def opposing_lane(i):
    return OPPOSING_EDGE - LANE * (i + 0.5)


def smooth(t, a, b):
    u = min(max((t - a) / (b - a), 0), 1)
    return u * u * (3 - 2 * u)


def material(name, color, roughness=0.6, metallic=0.0, emission=0.0, specular=0.5):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if hasattr(m, "use_nodes") and not m.node_tree:
        m.use_nodes = True
    nodes = m.node_tree.nodes
    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        out = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None) or nodes.new("ShaderNodeOutputMaterial")
        m.node_tree.links.new(bsdf.outputs[0], out.inputs[0])
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Emission Color"].default_value = (*color, 1)
    bsdf.inputs["Emission Strength"].default_value = emission
    bsdf.inputs["Specular IOR Level"].default_value = specular
    return m


def collection(name, parent=None):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        (parent or bpy.context.scene.collection).children.link(coll)
    return coll


def mesh_object(name, bm, mat, coll, parent=None, recalc=True):
    if recalc:
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0005)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    ob.parent = parent
    return ob


def ribbon(bm, edge_a, edge_b, s0, s1):
    n = max(1, round((s1 - s0) / STEP))
    uv = bm.loops.layers.uv.verify()
    prev = None
    for i in range(n + 1):
        s = s0 + (s1 - s0) * i / n
        cur = (bm.verts.new(road_point(s, *edge_a)), bm.verts.new(road_point(s, *edge_b)), s)
        if prev:
            face = bm.faces.new((prev[0], prev[1], cur[1], cur[0]))
            for loop, coord in zip(face.loops, ((edge_a[0], prev[2]), (edge_b[0], prev[2]), (edge_b[0], s), (edge_a[0], s))):
                loop[uv].uv = coord
        prev = cur


def profile(bm, points, s0, s1):
    for a, b in zip(points, points[1:] + points[:1]):
        ribbon(bm, a, b, s0, s1)


def road_box(bm, s, lateral, z, size):
    m = Matrix.Translation(road_point(s, lateral, z)) @ Matrix.Rotation(-heading(s), 4, "Z") @ Matrix.Diagonal((*size, 1))
    bmesh.ops.create_cube(bm, size=1, matrix=m)


def local_box(bm, center, size, tilt_x=0.0):
    m = Matrix.Translation(center) @ Matrix.Rotation(tilt_x, 4, "X") @ Matrix.Diagonal((*size, 1))
    bmesh.ops.create_cube(bm, size=1, matrix=m)


def torus(bm, matrix, major, minor, segments=32, ring=10):
    grid = [[bm.verts.new(matrix @ Vector(((major + minor * math.cos(v)) * math.cos(u), (major + minor * math.cos(v)) * math.sin(u), minor * math.sin(v))))
             for v in (2 * math.pi * j / ring for j in range(ring))]
            for u in (2 * math.pi * i / segments for i in range(segments))]
    for i in range(segments):
        for j in range(ring):
            bm.faces.new((grid[i][j], grid[(i + 1) % segments][j], grid[(i + 1) % segments][(j + 1) % ring], grid[i][(j + 1) % ring]))


def build_road(coll, mats):
    bm = bmesh.new()
    ribbon(bm, (-17.2, 0), (GUARDRAIL - 0.3, 0), 0, ROAD_END)
    mesh_object("Asphalt", bm, mats["asphalt"], coll, recalc=False)

    bm = bmesh.new()
    for lateral in (RIGHT_EDGE, OPPOSING_EDGE - LANE * 3):
        ribbon(bm, (lateral - 0.08, 0.01), (lateral + 0.08, 0.01), 0, ROAD_END)
    for lateral in (LANE, LANE * 2, OPPOSING_EDGE - LANE, OPPOSING_EDGE - LANE * 2):
        for s in range(0, ROAD_END, 12):
            ribbon(bm, (lateral - 0.08, 0.01), (lateral + 0.08, 0.01), s, s + 3)
    mesh_object("Markings_White", bm, mats["white"], coll, recalc=False)

    bm = bmesh.new()
    for lateral in (0.0, OPPOSING_EDGE):
        ribbon(bm, (lateral - 0.08, 0.01), (lateral + 0.08, 0.01), 0, ROAD_END)
    mesh_object("Markings_Yellow", bm, mats["yellow"], coll, recalc=False)

    bm = bmesh.new()
    profile(bm, [(MEDIAN - 0.3, 0), (MEDIAN - 0.1, 0.9), (MEDIAN + 0.1, 0.9), (MEDIAN + 0.3, 0)], 0, ROAD_END)
    mesh_object("Median_Barrier", bm, mats["concrete"], coll)

    bm = bmesh.new()
    for lateral in (GUARDRAIL, -17.5):
        profile(bm, [(lateral - 0.03, 0.5), (lateral - 0.03, 0.8), (lateral + 0.03, 0.8), (lateral + 0.03, 0.5)], 0, ROAD_END)
        post_offset = 0.08 if lateral > 0 else -0.08
        for s in range(0, ROAD_END, 4):
            road_box(bm, s, lateral + post_offset, 0.4, (0.1, 0.1, 0.8))
    mesh_object("Guardrails", bm, mats["metal"], coll)

    bm = bmesh.new()
    for s in range(30, ROAD_END, 55):
        road_box(bm, s, MEDIAN, 5.5, (0.2, 0.2, 11))
        road_box(bm, s, MEDIAN, 11, (5.5, 0.12, 0.12))
        for side in (-2.6, 2.6):
            road_box(bm, s, MEDIAN + side, 10.95, (0.9, 0.3, 0.12))
    mesh_object("Street_Lamps", bm, mats["metal"], coll)
    bm = bmesh.new()
    for s in range(30, ROAD_END, 55):
        for side in (-2.6, 2.6):
            road_box(bm, s, MEDIAN + side, 10.87, (0.7, 0.22, 0.04))
    mesh_object("Lamp_Lights", bm, mats["lamp"], coll)

    bm = bmesh.new()
    for lateral in (MEDIAN, GUARDRAIL + 0.6):
        road_box(bm, GANTRY_S, lateral, 3.75, (0.35, 0.35, 7.5))
    span = GUARDRAIL + 0.6 - MEDIAN
    road_box(bm, GANTRY_S, MEDIAN + span / 2, 7.3, (span, 0.4, 0.4))
    mesh_object("Gantry", bm, mats["metal"], coll)
    bm = bmesh.new()
    road_box(bm, GANTRY_S - 0.3, lane_center(2) + 0.5, 7.6, (5.6, 0.08, 2.4))
    mesh_object("Gantry_Sign", bm, mats["sign"], coll)

    bm = bmesh.new()
    road_box(bm, OVERPASS_S, 0, 7.4, (140, 11, 1.3))
    road_box(bm, OVERPASS_S, 0, 8.5, (140, 0.3, 0.9))
    for lateral in (MEDIAN, GUARDRAIL + 1.5, -19):
        road_box(bm, OVERPASS_S, lateral, 3.4, (1.1, 8, 6.8))
    mesh_object("Overpass", bm, mats["concrete"], coll)

    ground = bmesh.new()
    bmesh.ops.create_grid(ground, x_segments=1, y_segments=1, size=4000)
    bmesh.ops.translate(ground, verts=ground.verts, vec=(0, 0, -0.06))
    mesh_object("Ground", ground, mats["ground"], coll, recalc=False)


def tube(bm, a, b, radius_a, radius_b, sides=6):
    frame = (b - a).normalized().to_track_quat("Z", "Y")
    rings = [[bm.verts.new(c + frame @ Vector((r * math.cos(2 * math.pi * i / sides), r * math.sin(2 * math.pi * i / sides), 0))) for i in range(sides)]
             for c, r in ((a, radius_a), (b, radius_b))]
    for i in range(sides):
        bm.faces.new((rings[0][i], rings[0][(i + 1) % sides], rings[1][(i + 1) % sides], rings[1][i]))


LEAF_OUTLINE = (Vector((0, -1, 0)), Vector((0.5, 0, 0.12)), Vector((0, 1, 0)), Vector((-0.5, 0, 0.12)))


def leaf(bm, rng, center, size):
    turn = Euler((rng.uniform(0, math.pi), rng.uniform(0, math.pi), rng.uniform(0, 2 * math.pi))).to_matrix()
    bm.faces.new([bm.verts.new(center + turn @ (p * size)) for p in LEAF_OUTLINE]).material_index = 1


def leaf_cluster(bm, rng, center, radius, count, size):
    for _ in range(count):
        offset = Vector((rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 0.75))).normalized() * radius * rng.uniform(0.2, 1) ** 0.5
        leaf(bm, rng, center + offset, rng.uniform(*size))


def deciduous(rng, height, spread, leaves=300, leaf_size=(0.14, 0.25)):
    bm = bmesh.new()
    crown_base = Vector((rng.uniform(-0.5, 0.5), rng.uniform(-0.5, 0.5), height * rng.uniform(0.3, 0.42)))
    tube(bm, Vector((0, 0, -0.3)), crown_base, height * 0.035, height * 0.022, 8)
    tips = [(crown_base + Vector((0, 0, height * 0.45)), height * 0.2)]
    limbs = rng.randint(5, 7)
    for i in range(limbs):
        angle = 2 * math.pi * (i + rng.uniform(-0.3, 0.3)) / limbs
        lift = rng.uniform(0.45, 1.2)
        direction = Vector((math.cos(angle) * math.cos(lift) * spread, math.sin(angle) * math.cos(lift) * spread, math.sin(lift)))
        length = height * rng.uniform(0.3, 0.5)
        end = crown_base + direction * length
        tube(bm, crown_base, end, height * 0.014, height * 0.005, 5)
        tips.append((end, length * 0.5))
        for _ in range(rng.randint(2, 3)):
            start = crown_base.lerp(end, rng.uniform(0.35, 0.8))
            twig = (direction + Vector((rng.uniform(-0.8, 0.8), rng.uniform(-0.8, 0.8), rng.uniform(-0.2, 0.8)))).normalized()
            twig_end = start + twig * length * rng.uniform(0.4, 0.7)
            tube(bm, start, twig_end, height * 0.007, height * 0.003, 4)
            tips.append((twig_end, length * 0.42))
    for tip, radius in tips:
        leaf_cluster(bm, rng, tip, radius, leaves, leaf_size)
    return bm


def conifer(rng, height):
    bm = bmesh.new()
    tube(bm, Vector((0, 0, -0.3)), Vector((0, 0, height)), height * 0.02, 0.03, 8)
    base, reach = height * 0.2, height * rng.uniform(0.2, 0.26)
    z = base
    while z < height:
        radius = reach * (1 - (z - base) / (height - base)) ** 0.85 + 0.25
        boughs = rng.randint(5, 7)
        for i in range(boughs):
            angle = 2 * math.pi * (i + rng.uniform(0, 1)) / boughs
            end = Vector((math.cos(angle) * radius, math.sin(angle) * radius, z - radius * rng.uniform(0.15, 0.4)))
            start = Vector((0, 0, z))
            tube(bm, start, end, 0.05, 0.015, 3)
            for _ in range(int(radius * 9) + 3):
                p = start.lerp(end, rng.uniform(0.15, 1.05)) + Vector((rng.uniform(-0.3, 0.3), rng.uniform(-0.3, 0.3), rng.uniform(-0.3, 0.15)))
                leaf(bm, rng, p, rng.uniform(0.16, 0.3))
        z += rng.uniform(0.55, 0.9)
    return bm


def bush(rng, height):
    bm = bmesh.new()
    for _ in range(rng.randint(4, 6)):
        center = Vector((rng.uniform(-1.1, 1.1), rng.uniform(-1.1, 1.1), height * rng.uniform(0.3, 0.75)))
        tube(bm, Vector((0, 0, -0.1)), center, 0.04, 0.015, 3)
        leaf_cluster(bm, rng, center, height * 0.45, 110, (0.12, 0.24))
    return bm


def tree_variants(mats):
    rng = random.Random(3)
    recipes = {
        "deciduous": [lambda: deciduous(rng, 11, 1.0), lambda: deciduous(rng, 13, 1.15), lambda: deciduous(rng, 15, 0.9), lambda: deciduous(rng, 18, 1.0)],
        "roadside": [lambda: deciduous(rng, 12, 1.05, 800, (0.07, 0.13)), lambda: deciduous(rng, 15, 0.95, 800, (0.07, 0.13)), lambda: deciduous(rng, 17, 1.1, 800, (0.07, 0.13))],
        "conifer": [lambda: conifer(rng, 16), lambda: conifer(rng, 21)],
        "bush": [lambda: bush(rng, 2.2), lambda: bush(rng, 3.2)],
    }
    variants = {}
    for kind, makers in recipes.items():
        variants[kind] = []
        for i, make in enumerate(makers):
            bm = make()
            mesh = bpy.data.meshes.new(f"Tree_{kind}_{i}")
            bm.to_mesh(mesh)
            bm.free()
            mesh.materials.append(mats["bark"])
            mesh.materials.append(mats["leaves"])
            variants[kind].append(mesh)
    return variants


def scatter_trees(coll, variants):
    rng = random.Random(11)
    count = 0

    def plant(kind, s, lateral, scale):
        nonlocal count
        if abs(s - OVERPASS_S) < 10 and abs(lateral) < 75:
            return
        tree = bpy.data.objects.new(f"Tree_{count:04d}", rng.choice(variants[kind]))
        tree.location = road_point(s, lateral)
        tree.rotation_euler.z = rng.uniform(0, 2 * math.pi)
        tree.scale = (scale, scale, scale * rng.uniform(0.9, 1.15))
        coll.objects.link(tree)
        count += 1

    def row(kinds, s_range, lateral_range, spacing, scale):
        s = s_range[0] + rng.uniform(0, spacing[1])
        while s < s_range[1]:
            side = 1 if lateral_range[0] > 0 else -1
            plant(rng.choice(kinds), s, side * rng.uniform(abs(lateral_range[0]), abs(lateral_range[1])), rng.uniform(*scale))
            s += rng.uniform(*spacing)

    mixed = ["deciduous", "deciduous", "deciduous", "conifer"]
    row(["bush"], (0, ROAD_END), (GUARDRAIL + 1.5, GUARDRAIL + 3.5), (2.5, 5), (0.7, 1.4))
    row(["roadside", "roadside", "roadside", "conifer"], (0, ROAD_END), (GUARDRAIL + 5, GUARDRAIL + 9), (5, 9), (0.8, 1.2))
    row(mixed + ["conifer"], (0, ROAD_END), (GUARDRAIL + 12, GUARDRAIL + 20), (7, 12), (0.9, 1.4))
    row(mixed, (0, ROAD_END), (GUARDRAIL + 24, GUARDRAIL + 45), (9, 16), (1.0, 1.5))
    row(["bush"], (0, 320), (-19.5, -21), (3, 6), (0.7, 1.3))
    row(mixed, (0, 310), (-22, -27), (6, 10), (0.8, 1.2))
    row(mixed + ["conifer"], (0, 300), (-32, -48), (8, 14), (0.9, 1.4))
    row(["deciduous", "bush"], (310, ROAD_END), (-45, -110), (25, 45), (0.5, 0.9))
    return count


def leaf_material():
    mat = bpy.data.materials.get("Leaves") or bpy.data.materials.new("Leaves")
    if hasattr(mat, "use_nodes") and not mat.node_tree:
        mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    surface = tree.nodes.new("ShaderNodeBsdfPrincipled")
    surface.inputs["Base Color"].default_value = (0.03, 0.055, 0.018, 1)
    surface.inputs["Roughness"].default_value = 0.85
    surface.inputs["Specular IOR Level"].default_value = 0.15
    glow = tree.nodes.new("ShaderNodeBsdfTranslucent")
    glow.inputs["Color"].default_value = (0.3, 0.34, 0.07, 1)
    mix = tree.nodes.new("ShaderNodeMixShader")
    mix.inputs[0].default_value = 0.18
    tree.links.new(surface.outputs[0], mix.inputs[1])
    tree.links.new(glow.outputs[0], mix.inputs[2])
    tree.links.new(mix.outputs[0], tree.nodes.new("ShaderNodeOutputMaterial").inputs["Surface"])
    return mat


def glow_taillights(mat):
    tree = mat.node_tree
    bsdf = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
    base = bsdf.inputs["Base Color"].links[0].from_socket
    channels = tree.nodes.new("ShaderNodeSeparateColor")
    tree.links.new(base, channels.inputs[0])
    redness = math_node(tree, "SUBTRACT", channels.outputs[0], math_node(tree, "MAXIMUM", channels.outputs[1], channels.outputs[2]))
    mask = tree.nodes.new("ShaderNodeMapRange")
    mask.interpolation_type = "SMOOTHSTEP"
    mask.inputs["From Min"].default_value = 0.3
    mask.inputs["From Max"].default_value = 0.55
    mask.inputs["To Max"].default_value = TAILLIGHT_GLOW
    tree.links.new(redness, mask.inputs["Value"])
    tree.links.new(base, bsdf.inputs["Emission Color"])
    tree.links.new(mask.outputs[0], bsdf.inputs["Emission Strength"])


def load_model(name):
    mesh = bpy.data.meshes.get("Model_" + name)
    if mesh:
        return mesh
    filename, length = MODELS[name]
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=os.path.join(MODELS_DIR, filename))
    imported = [o for o in bpy.data.objects if o not in before]
    body = next(o for o in imported if o.type == "MESH")
    mesh = body.data
    mesh.transform(body.matrix_world)
    co = np.empty(len(mesh.vertices) * 3, "f")
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    lo, hi = co.min(0), co.max(0)
    mesh.transform(Matrix.Scale(length / (hi[1] - lo[1]), 4) @ Matrix.Translation((-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2])))
    mesh.name = "Model_" + name
    mesh.use_fake_user = True
    for ob in imported:
        bpy.data.objects.remove(ob)
    for mat in mesh.materials:
        glow_taillights(mat)
    split_wheels(mesh)
    return mesh


def find_wheels(co):
    ground = co[co[:, 2] < 0.04]
    wheels = []
    for side in (-1, 1):
        for end in (-1, 1):
            patch = ground[(ground[:, 0] * side > 0) & (ground[:, 1] * end > 0)]
            xin_p, xout_p = float((patch[:, 0] * side).min()), float((patch[:, 0] * side).max())
            z_bot = float(patch[:, 2].min())
            ay0 = float(np.median(patch[:, 1]))
            sidewall = co[(co[:, 0] * side > xin_p - 0.02) & (co[:, 0] * side < xout_p + 0.03) & (np.abs(co[:, 1] - ay0) < 0.25) & (co[:, 2] > 0.14) & (co[:, 2] < 0.32)]
            sw_out = float((sidewall[:, 0] * side).max())
            xo_lo, xo_hi = xin_p - 0.025, sw_out + 0.002
            x0, x1 = (xo_lo, xo_hi) if side > 0 else (-xo_hi, -xo_lo)

            tread = co[(co[:, 0] * side > xin_p + 0.02) & (co[:, 0] * side < xout_p - 0.02) & (np.abs(co[:, 1] - ay0) < 0.50) & (co[:, 2] < 0.90)]
            ty, tz = tread[:, 1], tread[:, 2]
            best = (-1, ay0, 0.38, 0.38)
            for cy in np.linspace(ay0 - 0.03, ay0 + 0.03, 13):
                dy2 = (ty - cy) ** 2
                for r in np.linspace(0.32, 0.45, 27):
                    cz = z_bot + r
                    score = int(np.sum(np.abs(np.sqrt(dy2 + (tz - cz) ** 2) - r) < 0.012))
                    if score > best[0]:
                        best = (score, cy, cz, r)
            _, cy0, cz0, r0 = best
            best = (-1, cy0, cz0, r0)
            for cy in np.linspace(cy0 - 0.015, cy0 + 0.015, 13):
                dy2 = (ty - cy) ** 2
                for cz in np.linspace(cz0 - 0.015, cz0 + 0.015, 13):
                    rad = np.sqrt(dy2 + (tz - cz) ** 2)
                    for r in np.linspace(r0 - 0.015, r0 + 0.015, 13):
                        if cz - r < z_bot - 0.008 or cz - r > z_bot + 0.012:
                            continue
                        score = int(np.sum(np.abs(rad - r) < 0.008))
                        if score > best[0]:
                            best = (score, cy, cz, r)
            _, cy, cz, radius = best
            rad_t = np.hypot(ty - cy, tz - cz)
            gap = min(np.arange(0.008, 0.026, 0.002), key=lambda d: int(np.sum((rad_t >= radius + d - 0.004) & (rad_t < radius + d + 0.004))))
            r_cut = max(float(radius + gap), float(cz - z_bot + 0.004))
            wheels.append((float(x0), float(x1), float(cy), float(cz), float(radius), float(r_cut)))
    return wheels


def split_wheels(mesh):
    co = np.empty(len(mesh.vertices) * 3, "f")
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.faces.ensure_lookup_table()
    taken = set()
    for k, (x0, x1, axle_y, axle_z, radius, r_cut) in enumerate(find_wheels(co)):
        inside = (co[:, 0] > x0) & (co[:, 0] < x1) & ((co[:, 1] - axle_y) ** 2 + (co[:, 2] - axle_z) ** 2 < r_cut ** 2)
        faces = {f.index for f in bm.faces if all(inside[v.index] for v in f.verts)}
        wheel = bm.copy()
        wheel.faces.ensure_lookup_table()
        bmesh.ops.delete(wheel, geom=[wheel.faces[i] for i in range(len(wheel.faces)) if i not in faces], context="FACES")
        bmesh.ops.translate(wheel, verts=wheel.verts, vec=(0, -axle_y, -axle_z))
        wheel_mesh = bpy.data.meshes.new(f"{mesh.name}_Wheel_{k}")
        wheel.to_mesh(wheel_mesh)
        wheel.free()
        for mat in mesh.materials:
            wheel_mesh.materials.append(mat)
        wheel_mesh.use_fake_user = True
        wheel_mesh["axle"] = (0.0, axle_y, axle_z)
        wheel_mesh["radius"] = radius
        taken |= faces
        print(mesh.name, "wheel", k, "axle", (round(axle_y, 3), round(axle_z, 3)), "radius", round(radius, 3), "cut", round(r_cut, 3), "faces", len(faces))
    bmesh.ops.delete(bm, geom=[bm.faces[i] for i in taken], context="FACES")
    bm.to_mesh(mesh)
    bm.free()


def build_ego_interior(coll, mats, rig):
    bm = bmesh.new()
    local_box(bm, Vector((0, 0.55, 0.93)), (1.8, 0.9, 0.25))
    local_box(bm, Vector((-0.33, 0.8, 1.045)), (0.44, 0.3, 0.08))
    torus(bm, Matrix.Translation((-0.33, 0.25, 0.985)) @ Matrix.Rotation(math.radians(68), 4, "X"), 0.19, 0.022)
    local_box(bm, Vector((0.015, 0.3, 1.378)), (0.21, 0.03, 0.07))
    local_box(bm, Vector((0.015, 0.33, 1.47)), (0.03, 0.03, 0.12))
    mesh_object("Ego_Interior", bm, mats["interior"], coll, parent=rig)
    bm = bmesh.new()
    local_box(bm, Vector((0.015, 0.283, 1.394)), (0.19, 0.001, 0.022))
    mesh_object("Ego_Mirror_Glass", bm, mats["mirror_sky"], coll, parent=rig)
    bm = bmesh.new()
    rows = [[bm.verts.new((x, y, 0.955 - 0.11 * (x / 0.9) ** 2 - 0.07 * ((y - 1.0) / 1.5) ** 2))
             for x in (-0.9 + 1.8 * i / 16 for i in range(17))]
            for y in (1.0 + 1.5 * j / 10 for j in range(11))]
    for near, far in zip(rows, rows[1:]):
        for i in range(16):
            bm.faces.new((near[i], near[i + 1], far[i + 1], far[i])).smooth = True
    mesh_object("Ego_Hood", bm, mats["hood"], coll, parent=rig, recalc=False)

    cam_data = bpy.data.cameras.new("DriverCam")
    cam_data.lens = 40
    cam_data.clip_start, cam_data.clip_end = 0.05, 4000
    cam_data.dof.use_dof = True
    cam_data.dof.focus_distance = 45
    cam_data.dof.aperture_fstop = 1.6
    cam = bpy.data.objects.new("DriverCam", cam_data)
    coll.objects.link(cam)
    cam.parent = rig
    cam.location = (0.05, -0.3, 1.22)
    cam.rotation_euler = (math.radians(92.3), 0, 0)
    bpy.context.scene.camera = cam
    shake_camera(cam)


def text_object(name, body, size, mat, coll, align="LEFT", shear=0.0):
    curve = bpy.data.curves.new(name, "FONT")
    curve.body = body
    curve.size = size
    curve.align_x = align
    curve.shear = shear
    if os.path.exists(FONT_PATH):
        curve.font = bpy.data.fonts.load(FONT_PATH, check_existing=True)
    curve.materials.append(mat)
    ob = bpy.data.objects.new(name, curve)
    coll.objects.link(ob)
    return ob


def arrow_mesh(bm, origin, size, angle):
    turn = Matrix.Translation(origin) @ Matrix.Rotation(angle, 4, "Z") @ Matrix.Scale(size, 4)
    outline = [(-0.09, 0), (0.09, 0), (0.09, 0.55), (0.3, 0.55), (0, 1), (-0.3, 0.55), (-0.09, 0.55)]
    bm.faces.new([bm.verts.new(turn @ Vector((x, y, 0))) for x, y in outline])


def disc(bm, center, radius):
    bmesh.ops.create_circle(bm, cap_ends=True, segments=20, radius=radius, matrix=Matrix.Translation(center))


def build_sign_text(coll, mats):
    def stand(ob, lateral, z):
        ob.location = road_point(GANTRY_S - 0.36, lateral, z)
        ob.rotation_euler = (math.pi / 2, 0, -heading(GANTRY_S))

    left = lane_center(2) + 0.5 - 2.5
    stand(text_object("Sign_Route", "ROUTE 47  /  EXIT 7", 0.26, mats["white"], coll), left, 8.35)
    stand(text_object("Sign_Place", "Pine Valley", 0.72, mats["white"], coll), left, 7.4)
    stand(text_object("Sign_Distance", "250 m", 0.3, mats["white"], coll), left, 6.75)
    bm = bmesh.new()
    arrow_mesh(bm, Vector((0, 0, 0)), 1.3, -math.pi / 4)
    stand(mesh_object("Sign_Arrow", bm, mats["white"], coll, recalc=False), left + 3.9, 6.95)


def build_hud(coll, mats, rig):
    hud = mats["hud"]
    root = bpy.data.objects.new("HUD", None)
    coll.objects.link(root)
    root.parent = rig
    root.location = HUD_CENTER
    root.rotation_euler = (math.pi / 2, 0, 0)
    root.scale = (HUD_SCALE,) * 3

    def mount(ob, x, z):
        ob.parent = root
        ob.location = (x - HUD_CENTER[0], z - HUD_CENTER[2], 0)
        return ob

    def switch(ob, start, end):
        keys = [(0.5, start > 1), (start - 0.5, False)]
        if end <= FRAMES:
            keys.append((end - 0.5, True))
        for frame, hidden in keys:
            ob.hide_render = ob.hide_viewport = hidden
            ob.keyframe_insert("hide_render", frame=frame)
            ob.keyframe_insert("hide_viewport", frame=frame)
        if ob.animation_data and ob.animation_data.action:
            for layer in ob.animation_data.action.layers:
                for strip in layer.strips:
                    for bag in strip.channelbags:
                        for fc in bag.fcurves:
                            for kp in fc.keyframe_points:
                                kp.interpolation = "CONSTANT"

    kmh = lambda frame: int(3.6 * (30 + 5 * (frame - 1) / FPS / 12) + 1e-6)
    shown = {}
    for frame in range(1, FRAMES + 1):
        shown.setdefault(kmh(frame), [frame, frame])[1] = frame
    for value, (start, end) in shown.items():
        switch(mount(text_object(f"HUD_Speed_{value}", str(value), 0.2, hud, coll, shear=0.25), 0.5, 1.27), start, end + 1)
    for gear, start, end in (("S4", 1, GEAR_SHIFT_FRAME), ("S5", GEAR_SHIFT_FRAME, FRAMES + 1)):
        switch(mount(text_object("HUD_Gear_" + gear, "|  " + gear, 0.085, hud, coll), 0.9, 1.29), start, end)
    mount(text_object("HUD_Unit", "KM/H", 0.04, hud, coll), 0.52, 1.2)
    mount(text_object("HUD_Temp", "10°", 0.1, hud, coll), -0.62, 1.28)
    mount(text_object("HUD_Info", "Pine Valley\nFriday\nCloudy", 0.034, hud, coll), -0.38, 1.345)

    bm = bmesh.new()
    arrow_mesh(bm, Vector((1.18, 1.285, 0)), 0.1, 0)
    for i in range(22):
        u = i / 21
        disc(bm, Vector((0.48 + 0.6 * u, 1.42 + 0.05 * math.sin(math.pi * u), 0)), 0.006)
    for x, y, r in ((-0.76, 1.31, 0.03), (-0.72, 1.33, 0.04), (-0.68, 1.31, 0.03)):
        disc(bm, Vector((x, y, 0)), r)
    mount(mesh_object("HUD_Icons", bm, hud, coll, recalc=False), 0, 0)


def build_windshield_bokeh(coll, rig):
    mat = bpy.data.materials.get("Bokeh") or bpy.data.materials.new("Bokeh")
    if hasattr(mat, "use_nodes") and not mat.node_tree:
        mat.use_nodes = True
    mat.surface_render_method = "BLENDED"
    tree = mat.node_tree
    tree.nodes.clear()
    toward = tree.nodes.new("ShaderNodeVectorMath")
    toward.operation = "DOT_PRODUCT"
    tree.links.new(tree.nodes.new("ShaderNodeNewGeometry").outputs["Incoming"], toward.inputs[0])
    toward.inputs[1].default_value = -TOWARD_SUN
    glow = tree.nodes.new("ShaderNodeEmission")
    glow.inputs["Color"].default_value = (1.0, 0.62, 0.28, 1)
    tree.links.new(math_node(tree, "MULTIPLY", math_node(tree, "POWER", math_node(tree, "MAXIMUM", toward.outputs["Value"], 0.0), 22.0), BOKEH_GLOW), glow.inputs["Strength"])
    additive = tree.nodes.new("ShaderNodeAddShader")
    tree.links.new(glow.outputs[0], additive.inputs[0])
    tree.links.new(tree.nodes.new("ShaderNodeBsdfTransparent").outputs[0], additive.inputs[1])
    tree.links.new(additive.outputs[0], tree.nodes.new("ShaderNodeOutputMaterial").inputs["Surface"])

    rng = random.Random(5)
    bm = bmesh.new()
    for i in range(46):
        center = Vector((rng.uniform(-0.5, 0.6), 0.45 + i * 0.0004, rng.uniform(1.1, 1.5)))
        bmesh.ops.create_circle(bm, cap_ends=True, segments=28, radius=rng.uniform(0.006, 0.014),
                                matrix=Matrix.Translation(center) @ Matrix.Rotation(math.pi / 2, 4, "X"))
    spots = mesh_object("Windshield_Bokeh", bm, mat, coll, parent=rig, recalc=False)
    spots.visible_shadow = False


def shake_camera(cam):
    rng = random.Random(9)
    waves = [(rng.uniform(0.5, 4.0), rng.uniform(0, 6.28), rng.uniform(0, 6.28)) for _ in range(6)]
    base_rotation, base_height = cam.rotation_euler.copy(), cam.location.z
    for frame in range(1, FRAMES + 1):
        t = (frame - 1) / FPS
        pitch = sum(math.sin(2 * math.pi * hz * t + a) / hz for hz, a, _ in waves)
        yaw = sum(math.sin(2 * math.pi * hz * t + b) / hz for hz, _, b in waves)
        cam.rotation_euler = (base_rotation.x + pitch * CAMERA_SHAKE, 0, base_rotation.z + yaw * CAMERA_SHAKE * 0.6)
        cam.location.z = base_height + pitch * 0.0015
        cam.keyframe_insert("rotation_euler", frame=frame)
        cam.keyframe_insert("location", frame=frame)


def asphalt_material():
    mat = material("Asphalt", (0.02, 0.02, 0.022), 0.35)
    tree = mat.node_tree
    link = tree.links.new
    bsdf = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
    road_uv = tree.nodes.new("ShaderNodeTexCoord").outputs["UV"]

    def noise(scale, detail, stretch):
        mapping = tree.nodes.new("ShaderNodeMapping")
        mapping.inputs["Scale"].default_value = stretch
        link(road_uv, mapping.inputs["Vector"])
        node = tree.nodes.new("ShaderNodeTexNoise")
        node.inputs["Scale"].default_value = scale
        node.inputs["Detail"].default_value = detail
        link(mapping.outputs[0], node.inputs["Vector"])
        return node.outputs[0]

    def remap(value, lo, hi, out_lo, out_hi):
        node = tree.nodes.new("ShaderNodeMapRange")
        node.interpolation_type = "SMOOTHSTEP"
        for key, v in (("From Min", lo), ("From Max", hi), ("To Min", out_lo), ("To Max", out_hi)):
            node.inputs[key].default_value = v
        link(value, node.inputs["Value"])
        return node.outputs[0]

    lateral = tree.nodes.new("ShaderNodeSeparateXYZ")
    link(road_uv, lateral.inputs[0])
    across_lane = math_node(tree, "FRACT", math_node(tree, "DIVIDE", lateral.outputs[0], LANE), 0.0)
    off_track = math_node(tree, "ABSOLUTE", math_node(tree, "SUBTRACT", math_node(tree, "ABSOLUTE", math_node(tree, "SUBTRACT", across_lane, 0.5), 0.0), 0.23), 0.0)
    polish = math_node(tree, "MULTIPLY", remap(off_track, 0.03, 0.14, 1.0, 0.0), remap(noise(0.6, 3.0, (1.0, 0.05, 1.0)), 0.3, 0.7, 0.4, 1.0))
    wear = remap(noise(0.5, 6.0, (1.0, 0.12, 1.0)), 0.35, 0.7, 0.36, 0.58)
    link(math_node(tree, "SUBTRACT", wear, math_node(tree, "MULTIPLY", polish, 0.2)), bsdf.inputs["Roughness"])

    tone = tree.nodes.new("ShaderNodeMix")
    tone.data_type = "RGBA"
    tone.inputs["A"].default_value = (0.03, 0.029, 0.03, 1)
    tone.inputs["B"].default_value = (0.012, 0.012, 0.013, 1)
    link(math_node(tree, "MAXIMUM", polish, remap(noise(0.25, 4.0, (1.0, 0.3, 1.0)), 0.45, 0.75, 0.0, 0.7)), tone.inputs["Factor"])
    link(tone.outputs["Result"], bsdf.inputs["Base Color"])

    grain = tree.nodes.new("ShaderNodeBump")
    grain.inputs["Strength"].default_value = 0.06
    link(noise(60.0, 2.0, (1.0, 1.0, 1.0)), grain.inputs["Height"])
    link(grain.outputs[0], bsdf.inputs["Normal"])
    return mat


def ego_s(t):
    return 20 + 30 * t + (5 / 24) * t * t


VEHICLES = [
    ("Ego", None, ego_s, lambda t: lane_center(0), False),
    ("Coupe", "Coupe", lambda t: 65 + 26.7 * t, lambda t: lane_center(1), False),
    ("SUV", "SUV", lambda t: 74 + 29.9 * t, lambda t: lane_center(2), False),
    ("Sedan", "Sedan", lambda t: 95 + 34 * t, lambda t: lane_center(0), False),
    ("FarA", "Sedan", lambda t: 170 + 31 * t, lambda t: lane_center(2), False),
    ("OppA", "SUV", lambda t: 224 - 31 * t, lambda t: opposing_lane(0), True),
    ("OppB", "Sedan", lambda t: 470 - 31 * t, lambda t: opposing_lane(1), True),
    ("OppC", "Coupe", lambda t: 690 - 31 * t, lambda t: opposing_lane(0), True),
]


def build_vehicles(coll, mats):
    rigs, wheels = [], []
    for name, model, s_of, lateral_of, reverse in VEHICLES:
        rig = bpy.data.objects.new("Rig_" + name, None)
        rig.empty_display_size = 0.5
        coll.objects.link(rig)
        if model:
            body = bpy.data.objects.new(name + "_Body", load_model(model))
            coll.objects.link(body)
            body.parent = rig
            for k in range(4):
                wheel_mesh = bpy.data.meshes[f"Model_{model}_Wheel_{k}"]
                wheel = bpy.data.objects.new(f"{name}_Wheel_{k}", wheel_mesh)
                coll.objects.link(wheel)
                wheel.parent = rig
                wheel.location = wheel_mesh["axle"]
                wheels.append((wheel, s_of, wheel_mesh.get("radius", wheel_mesh["axle"][2])))
        else:
            build_ego_interior(coll, mats, rig)
            build_hud(coll, mats, rig)
            build_windshield_bokeh(coll, rig)
        rigs.append((rig, s_of, lateral_of, reverse))
    for frame in range(1, FRAMES + 1):
        t = (frame - 1) / FPS
        for rig, s_of, lateral_of, reverse in rigs:
            s = s_of(t)
            rig.location = road_point(s, lateral_of(t))
            rig.rotation_euler.z = -heading(s) + (math.pi if reverse else 0)
            rig.keyframe_insert("location", frame=frame)
            rig.keyframe_insert("rotation_euler", frame=frame)
        for wheel, s_of, radius in wheels:
            wheel.rotation_euler.x = -abs(s_of(t) - s_of(0)) / radius
            wheel.keyframe_insert("rotation_euler", frame=frame)


def math_node(tree, operation, a, b):
    node = tree.nodes.new("ShaderNodeMath")
    node.operation = operation
    for socket, value in zip(node.inputs, (a, b)):
        if isinstance(value, (int, float)):
            socket.default_value = value
        else:
            tree.links.new(value, socket)
    return node.outputs[0]


def build_sky(scene, toward_sun):
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    if hasattr(world, "use_nodes") and not world.node_tree:
        world.use_nodes = True
    tree = world.node_tree
    tree.nodes.clear()
    link = tree.links.new

    direction = tree.nodes.new("ShaderNodeVectorMath")
    direction.operation = "NORMALIZE"
    link(tree.nodes.new("ShaderNodeTexCoord").outputs["Generated"], direction.inputs[0])

    separate = tree.nodes.new("ShaderNodeSeparateXYZ")
    link(direction.outputs[0], separate.inputs[0])
    ramp = tree.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[0].color = (*SKY_HORIZON, 1)
    ramp.color_ramp.elements[1].position = 0.35
    ramp.color_ramp.elements[1].color = (*SKY_ZENITH, 1)
    link(separate.outputs[2], ramp.inputs[0])
    sky = tree.nodes.new("ShaderNodeBackground")
    link(ramp.outputs[0], sky.inputs[0])
    stretch = tree.nodes.new("ShaderNodeMapping")
    stretch.inputs["Scale"].default_value = (1.5, 1.5, 9.0)
    link(direction.outputs[0], stretch.inputs["Vector"])
    cloud = tree.nodes.new("ShaderNodeTexNoise")
    cloud.inputs["Scale"].default_value = 2.5
    cloud.inputs["Detail"].default_value = 5.0
    link(stretch.outputs[0], cloud.inputs["Vector"])
    streaks = tree.nodes.new("ShaderNodeMapRange")
    streaks.interpolation_type = "SMOOTHSTEP"
    streaks.inputs["From Min"].default_value = 0.5
    streaks.inputs["From Max"].default_value = 0.75
    streaks.inputs["To Min"].default_value = 1.0
    streaks.inputs["To Max"].default_value = 1.0 + CLOUD_GAIN
    link(cloud.outputs[0], streaks.inputs["Value"])
    link(streaks.outputs[0], sky.inputs[1])

    facing = tree.nodes.new("ShaderNodeVectorMath")
    facing.operation = "DOT_PRODUCT"
    link(direction.outputs[0], facing.inputs[0])
    facing.inputs[1].default_value = toward_sun
    cos_angle = math_node(tree, "MAXIMUM", facing.outputs["Value"], 0.0)
    disc = math_node(tree, "MULTIPLY", math_node(tree, "POWER", cos_angle, 3000.0), SUN_DISC)
    halo = math_node(tree, "MULTIPLY", math_node(tree, "POWER", cos_angle, 40.0), SUN_HALO)
    glow = tree.nodes.new("ShaderNodeBackground")
    glow.inputs[0].default_value = (*SUN_COLOR, 1)
    link(math_node(tree, "ADD", disc, halo), glow.inputs[1])

    add = tree.nodes.new("ShaderNodeAddShader")
    link(sky.outputs[0], add.inputs[0])
    link(glow.outputs[0], add.inputs[1])
    link(add.outputs[0], tree.nodes.new("ShaderNodeOutputWorld").inputs["Surface"])


def build_haze(coll):
    mat = bpy.data.materials.get("Haze") or bpy.data.materials.new("Haze")
    if hasattr(mat, "use_nodes") and not mat.node_tree:
        mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    height = tree.nodes.new("ShaderNodeSeparateXYZ")
    tree.links.new(tree.nodes.new("ShaderNodeNewGeometry").outputs["Position"], height.inputs[0])
    falloff = math_node(tree, "EXPONENT", math_node(tree, "MULTIPLY", height.outputs[2], -1 / HAZE_SCALE_HEIGHT), 0.0)
    scatter = tree.nodes.new("ShaderNodeVolumeScatter")
    scatter.inputs["Color"].default_value = (*HAZE_COLOR, 1)
    scatter.inputs["Anisotropy"].default_value = 0.88
    tree.links.new(math_node(tree, "MULTIPLY", falloff, HAZE_DENSITY), scatter.inputs["Density"])
    tree.links.new(scatter.outputs[0], tree.nodes.new("ShaderNodeOutputMaterial").inputs["Volume"])

    xs, ys = [p.x for p in CENTER], [p.y for p in CENTER]
    lo = Vector((min(xs) - 400, min(ys) - 100, -1))
    hi = Vector((max(xs) + 400, max(ys) + 400, HAZE_SCALE_HEIGHT * 5))
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1, matrix=Matrix.Translation((lo + hi) / 2) @ Matrix.Diagonal((*(hi - lo), 1)))
    haze = mesh_object("Haze", bm, mat, coll, recalc=False)
    haze.display_type = "BOUNDS"


def build_light(coll):
    scene = bpy.context.scene
    data = bpy.data.lights.new("Sun", "SUN")
    data.energy = SUN_ENERGY
    data.color = SUN_COLOR
    data.angle = math.radians(1.5)
    sun = bpy.data.objects.new("Sun", data)
    coll.objects.link(sun)
    sun.rotation_euler = TOWARD_SUN.to_track_quat("Z", "Y").to_euler()
    build_sky(scene, TOWARD_SUN)
    build_haze(coll)

    eevee = scene.eevee
    eevee.use_volume_custom_range = True
    eevee.volumetric_start, eevee.volumetric_end = 3.0, 800
    eevee.volumetric_tile_size = "2"
    eevee.volumetric_samples = 256
    eevee.volumetric_sample_distribution = 0.9
    eevee.use_volumetric_shadows = True
    eevee.use_raytracing = True
    eevee.bokeh_max_size = 120
    eevee.taa_render_samples = 32
    scene.render.use_motion_blur = True
    scene.render.motion_blur_shutter = 0.5
    eevee.motion_blur_steps = 8
    build_compositor(scene)


def build_compositor(scene):
    group = bpy.data.node_groups.get("DriveComp") or bpy.data.node_groups.new("DriveComp", "CompositorNodeTree")
    group.nodes.clear()
    if not group.interface.items_tree:
        group.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    layers = group.nodes.new("CompositorNodeRLayers")
    bloom = group.nodes.new("CompositorNodeGlare")
    bloom.inputs["Type"].default_value = "Bloom"
    bloom.inputs["Threshold"].default_value = 2.5
    bloom.inputs["Strength"].default_value = BLOOM_STRENGTH
    bloom.inputs["Size"].default_value = 0.7
    group.links.new(layers.outputs["Image"], bloom.inputs["Image"])
    lens = group.nodes.new("CompositorNodeLensdist")
    lens.inputs["Dispersion"].default_value = LENS_DISPERSION
    group.links.new(bloom.outputs["Image"], lens.inputs["Image"])
    group.links.new(lens.outputs["Image"], group.nodes.new("NodeGroupOutput").inputs[0])
    scene.compositing_node_group = group
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = EXPOSURE


def build():
    scene = bpy.context.scene
    coll = collection("Drive")
    tree_coll = collection("Drive_Trees", coll)
    for ob in list(coll.objects) + list(tree_coll.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for name in ("Cube", "Light", "Camera"):
        ob = bpy.data.objects.get(name)
        if ob and coll not in ob.users_collection:
            bpy.data.objects.remove(ob, do_unlink=True)
    bpy.data.orphans_purge(do_recursive=True)

    scene.render.fps = FPS
    scene.frame_start, scene.frame_end = 1, FRAMES
    scene.render.resolution_x, scene.render.resolution_y = 1280, 720

    mats = {
        "asphalt": asphalt_material(),
        "lamp": material("Lamp_Light", (1.0, 0.72, 0.4), 0.5, 0.0, 60.0),
        "hud": material("HUD", (0.9, 0.97, 1.0), 0.5, 0.0, 6.0),
        "white": material("Paint_White", (0.75, 0.75, 0.72), 0.5),
        "yellow": material("Paint_Yellow", (0.8, 0.55, 0.05), 0.5),
        "concrete": material("Concrete", (0.45, 0.44, 0.42), 0.8),
        "metal": material("Galvanized", (0.4, 0.4, 0.42), 0.6, 0.6),        "mirror_sky": material("Mirror_Sky", (0.35, 0.2, 0.1), 1.0, 0.0, 0.35, specular=0.0),
        "sign": material("Sign_Green", (0.02, 0.18, 0.09), 0.5),
        "ground": material("Ground", (0.03, 0.035, 0.02), 0.9),
        "bark": material("Bark", (0.05, 0.035, 0.025), 0.9),
        "leaves": leaf_material(),
        "interior": material("Interior", (0.008, 0.008, 0.008), 0.6, specular=0.05),
        "hood": material("Ego_Paint", (0.6, 0.6, 0.6), 0.25, 0.9),
    }
    build_road(coll, mats)
    build_sign_text(coll, mats)
    trees = scatter_trees(tree_coll, tree_variants(mats))
    build_vehicles(coll, mats)
    build_light(coll)
    scene.frame_set(1)
    print("built", len(coll.objects), "objects and", trees, "trees; engine", scene.render.engine)


build()
