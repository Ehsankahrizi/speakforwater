import { defineCollection, z } from "astro:content";

const episodes = defineCollection({
  type: "content",
  schema: z.object({
    episode_number: z.number(),
    title: z.string(),
    description: z.string(),
    pub_date: z.coerce.date(),
    duration: z.string(),
    audio_url: z.string(),
    audio_size_bytes: z.number().optional(),
    paper: z.object({
      title: z.string(),
      authors: z.array(z.string()).optional(),
      journal: z.string().optional(),
      doi: z.string().optional(),
      url: z.string(),
      open_access: z.boolean().default(true),
    }),
    topics: z.array(z.string()).default([]),
    cover: z.string().optional(),
    transcript: z.string().optional(),
    chapters: z
      .array(
        z.object({
          start: z.string(),
          title: z.string(),
        })
      )
      .optional(),
    featured: z.boolean().default(false),
  }),
});

export const collections = { episodes };
